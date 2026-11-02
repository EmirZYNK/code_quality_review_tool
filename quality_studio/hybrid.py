"""Hibrit birleştirme: statik (AST), LLM ve dinamik (Docker/yerel) sonuçlarını tek kayıtta toplar.

Zincir: LLM önerir -> AST doğrular -> çalıştırma ölçer. Bu modül üç katmanın sonucunu birleştirir:

  * `review_and_fix`: TEK LLM çağrısı (bulgu doğrulama/eleme + açıklama + AST'nin kaçırdıkları + düzeltme + test
    girdisi önerisi); çıktının tamamı doğrulanır. Elenen bulgu silinmez, gerekçesiyle gösterilir.
  * `build_cards`: her bulgu için Statik / LLM / Dinamik rozetleri (hangi katman neyi doğruladı).
  * `overall_verdict`: statik doğrulama + çıktı eşitliği + hız birlikte tek karar.

Dürüstlük ilkeleri:
  * Dinamik ölçüm yalnızca verilen test girdileri için geçerlidir; "her durumda aynı" kanıtı değildir.
  * Dinamik katman bir bulgunun giderildiğini kanıtlamaz; davranışın korunduğunu ve hızın değiştiğini gösterir.
  * Fonksiyonu olmayan kod (düz script) ölçülemez; bu açıkça söylenir.
"""
from __future__ import annotations

import ast
import json
from collections import Counter

from . import llm
from .benchmark import MAX_CASE_CHARS, MAX_CASES, summarize
from .config import Config
from .engine import analyze
from .metrics import quality_score
from .models import Finding
from .rules import RULES

HYBRID_TASK = "hybrid"

# LLM'in her AST bulgusu için verebileceği kararlar. Elenen (yanlış pozitif) bulgu silinmez; gerekçesiyle gösterilir.
VERDICTS = ("onayla", "yanlis_pozitif", "baglama_bagli")
VERDICT_TR = {"onayla": "Onaylandı", "yanlis_pozitif": "Elendi (yanlış pozitif)", "baglama_bagli": "Bağlama bağlı"}
MIN_REASON_CHARS = 5   # gerekçesiz karar kabul edilmez
MAX_EXTRA_ISSUES = 5   # AST'nin kaçırdığı, LLM'in eklediği anlamsal sorunların üst sınırı
RISKY_SEVERITIES = ("critical", "high")  # bu önemdeki bir bulgu elenirse arayüz ayrıca uyarır


# --------------------------------------------------------------------------- fonksiyonlar ve test girdileri

def top_level_functions(code: str) -> list[str]:
    """Genel (alt çizgiyle başlamayan) üst düzey fonksiyon adları; kod geçersizse boş liste."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]


def common_functions(before: str, after: str) -> list[str]:
    """İki sürümde de bulunan genel üst düzey fonksiyonlar (Önce sürümündeki sırayla)."""
    later = set(top_level_functions(after))
    return [name for name in top_level_functions(before) if name in later]


def measurability(before: str, after: str) -> dict:
    """Dinamik ölçüm yapılabilir mi? {"functions": [...], "reason": None | "neden ölçülemez"}."""
    if not top_level_functions(before):
        return {"functions": [], "reason": "ölçülemez, çünkü çağrılacak fonksiyon yok (kod düz bir script)."}
    if not top_level_functions(after):
        return {"functions": [], "reason": "Sonra sürümünde çağrılacak bir fonksiyon yok ya da kod geçerli değil."}
    common = common_functions(before, after)
    if not common:
        return {"functions": [], "reason": "iki sürümde ortak bir fonksiyon adı yok (ad değişmiş olabilir)."}
    return {"functions": common, "reason": None}


def _clean_case(record, index: int) -> dict | None:
    """Tek bir test girdisini {label, args, kwargs} biçimine getirir; geçersizse None."""
    if isinstance(record, list):
        record = {"args": record}
    if not isinstance(record, dict):
        return None
    args, kwargs = record.get("args", []), record.get("kwargs", {})
    if not (isinstance(args, list) and isinstance(kwargs, dict)):
        return None
    label = str(record.get("etiket") or record.get("label") or f"girdi {index}")[:40]
    case = {"label": label, "args": args, "kwargs": kwargs}
    function = record.get("fonksiyon") or record.get("function")
    if function is not None:
        if not (isinstance(function, str) and function.isidentifier() and not function.startswith("_")):
            return None
        case["function"] = function
    try:
        size = len(json.dumps(case, ensure_ascii=False))
    except (TypeError, ValueError):
        return None
    return case if size <= MAX_CASE_CHARS else None


def sanitize_cases(raw) -> tuple[list[dict], int]:
    """Ham liste -> (geçerli test girdileri, reddedilen sayısı). En fazla MAX_CASES girdi alınır."""
    if raw is None:
        return [], 0
    if not isinstance(raw, list):
        return [], 1
    cases, rejected = [], 0
    for record in raw:
        case = _clean_case(record, len(cases) + 1)
        if case is None:
            rejected += 1
        elif len(cases) < MAX_CASES:
            cases.append(case)
    return cases, rejected


def cases_to_text(cases: list[dict]) -> str:
    """Test girdilerini arayüzdeki düzenlenebilir JSON metnine çevirir."""
    shown = [{"etiket": c["label"], **({"fonksiyon": c["function"]} if c.get("function") else {}),
              "args": c["args"], "kwargs": c["kwargs"]} for c in cases]
    return json.dumps(shown, ensure_ascii=False, indent=1)


def parse_cases(text: str) -> tuple[list[dict], str | None]:
    """Kullanıcının yazdığı JSON metnini doğrular. (girdiler, hata mesajı) döndürür."""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return [], f"Geçersiz JSON: {exc}"
    cases, rejected = sanitize_cases(raw)
    if rejected or not cases:
        return [], ('Test girdileri geçersiz. Biçim: [{"etiket": "tipik", "args": [5], "kwargs": {}}] '
                    "(args dizi, kwargs nesne olmalı).")
    return cases, None


# --------------------------------------------------------------------------- değişen kod ve test kapsamı

def _units(code: str) -> dict[str, str] | None:
    """Üst düzey fonksiyon ve sınıfların ad -> AST dökümü (modül düzeyi kodu ölçüm betiği ayrıca karşılaştırır)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    units: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            units[node.name] = ast.dump(node, include_attributes=False)
    return units


def changed_units(before: str, after: str) -> dict:
    """Önce ve Sonra arasında GERÇEKTEN değişen birimler (yorum ve boşluk değişikliği sayılmaz).

    Döndürür: {"public": [...ölçülebilir genel fonksiyonlar], "indirect": [...özel fonksiyon/sınıf/modül kodu],
    "added": [...yalnızca Sonra'da olanlar]}. Yalnızca "public" fonksiyonlar doğrudan çağrılıp sınanabilir.
    """
    old, new = _units(before), _units(after)
    if old is None or new is None:
        return {"public": [], "indirect": [], "added": []}
    callable_names = set(top_level_functions(before)) & set(top_level_functions(after))
    public, indirect = [], []
    for name, dump in old.items():
        if name in new and new[name] != dump:
            (public if name in callable_names else indirect).append(name)
    added = [n for n in new if n not in old]
    return {"public": public, "indirect": indirect, "added": added}


def test_coverage(before: str, after: str, cases: list[dict], default_function: str | None = None) -> dict:
    """Değişen genel fonksiyonlardan hangileri en az bir test girdisiyle sınanıyor?

    `cases` içinde `function` yazmayan girdiler `default_function` için sayılır.
    Döndürür: {"changed", "tested", "untested", "indirect", "complete"}.
    """
    changed = changed_units(before, after)
    tested_names = {c.get("function") or default_function for c in cases} - {None}
    tested = [n for n in changed["public"] if n in tested_names]
    untested = [n for n in changed["public"] if n not in tested_names]
    return {"changed": changed["public"], "tested": tested, "untested": untested,
            "indirect": changed["indirect"], "complete": not untested}


def coverage_text(coverage: dict) -> str:
    """Kapsamı tek cümlede anlatır (arayüz ve karar kartı için)."""
    total = len(coverage["changed"])
    if not total:
        return "Doğrudan sınanacak değişmiş genel fonksiyon yok."
    text = f"Değişen {total} genel fonksiyonun {len(coverage['tested'])}'i sınandı"
    if coverage["untested"]:
        text += f"; sınanmayan: {', '.join(coverage['untested'])}"
    return text + "."


# --------------------------------------------------------------------------- tek LLM çağrısı

def _rule_guidance(findings: list[dict]) -> list[dict]:
    """LLM'e bulgularla birlikte kuralın resmi düzeltme önerisini de verir (kurallara uysun diye)."""
    items = []
    for f in findings:
        meta = RULES.get(f["rule"])
        items.append({"rule": f["rule"], "line": f["line"], "message": f["message"], "onem": f["severity"],
                      "guven": f["confidence"], "kural_onerisi": meta.fix if meta else ""})
    return items


def _prompt(code: str, items: list[dict]) -> str:
    return (
        llm.BEHAVIOR_RULE +
        "Aşağıda bir Python kodu ve statik analizin (AST) bulguları var. TEK yanıtta dört iş yap:\n"
        "1) DOĞRULA ya da ELE: her bulgu için bu kod bağlamında karar ver. 'karar' şunlardan biri olmalı: "
        "'onayla' (bu kodda gerçek bir sorun), 'yanlis_pozitif' (kural tetiklendi ama bu kodda sorun değil), "
        "'baglama_bagli' (girdiye ya da çalışma ortamına bağlı; neye bağlı olduğunu söyle). "
        "'gerekce': kararın nedeni, koda özgü 1-2 cümle (zorunlu). 'neden': sorun neden önemli (1-2 cümle). "
        "'duzeltme': bu kodda nasıl düzelir (1-2 cümle; yanlis_pozitif için boş bırakabilirsin). "
        "Emin değilsen 'onayla' ya da 'baglama_bagli' de; özellikle güvenlik bulgularını yalnızca açık bir "
        "gerekçen varsa ele (ör. eval yalnızca sabit bir metne uygulanıyorsa).\n"
        "2) AST'nin KAÇIRDIĞI anlamsal sorunları 'ek_sorunlar' içinde ver (en fazla 5; emin değilsen verme; "
        "AST bulgularını tekrar yazma). Örnekler: işini yansıtmayan yanıltıcı fonksiyon/değişken adı, yorumla kodun "
        "çelişmesi, farklı yazılmış ama aynı işi yapan tekrar, gereksiz soyutlama, gereksiz savunmacı kod, mantık "
        "hatası. Her kayıtta 'alinti' o satırdan BİREBİR kod parçası olmalı (koda uymayan kayıt atılır).\n"
        "3) Kodu doğrulanan bulguları giderecek şekilde düzelt. Kurallar: davranışı koru, fonksiyon adlarını ve "
        "imzalarını değiştirme, her bulgunun 'kural_onerisi' alanına uy, gereksiz yeniden yazma yapma, yeni bağımlılık "
        "ekleme (standart kütüphane serbest). 'yanlis_pozitif' dediğin bulgu için koda DOKUNMA; hiçbir şeyi "
        "düzeltmek gerekmiyorsa 'kod' alanına kodu aynen yaz. Hız önemli: bulguyla doğrudan ilgili olmasa bile, "
        "çıktıyı BİREBİR (sıra ve tekrarlar dahil) koruyarak yapılabiliyorsa belirgin algoritmik verimsizlikleri "
        "(iç içe döngüyle arama, döngüde .count(), döngüde liste büyütme) en verimli yöntemle (set/dict/Counter, "
        "comprehension) yaz. Düzeltmen yeni bir bulgu doğurmasın (ör. daha derin iç içe blok açma).\n"
        "4) Düzeltmenin davranışını sınamak için test girdileri üret. 'fonksiyon': en çok test edeceğin üst düzey genel "
        "fonksiyonun adı (uygun fonksiyon yoksa null). 'test_girdileri': en fazla 16 kayıt. DEĞİŞTİRDİĞİN HER genel "
        "üst düzey fonksiyon için, kaydın 'fonksiyon' alanına o fonksiyonun adını yazarak en az 2 girdi ver (bir sınır "
        "durumu: boş/sıfır/negatif, bir tipik durum) ve verimsizliği düzelttiysen her fonksiyon için orta büyüklükte bir "
        "girdi ekle (ör. 300 elemanlı liste) ki hız farkı görünsün. Test girdisi olmayan değişmiş fonksiyon 'kabul' "
        "alamaz. Girdiler JSON ile yazılabilmeli (args dizi, kwargs nesne). Doğru çıktıyı bilmen gerekmez. "
        "Dosya, ağ ya da uzun süren girdi verme.\n"
        'JSON biçimi: {"aciklamalar": [{"rule": "...", "line": 0, "karar": "onayla | yanlis_pozitif | baglama_bagli", '
        '"gerekce": "...", "neden": "...", "duzeltme": "..."}], '
        '"ek_sorunlar": [{"line": 0, "alinti": "o satırdan birebir kod parçası", "kategori": "'
        + " | ".join(llm.CANDIDATE_CATEGORIES) + '", "baslik": "kısa", "neden": "1 cümle", "duzeltme": "1 cümle"}], '
        '"kod": "tam düzeltilmiş kod", "degisiklikler": ["kısa Türkçe madde"], "fonksiyon": "ad", '
        '"test_girdileri": [{"etiket": "kısa ad", "fonksiyon": "ad", "args": [], "kwargs": {}}]}\n'
        "Her bulgu için tam olarak bir açıklama kaydı ver; rule ve line değerlerini aynen kopyala.\n\n"
        f"BULGULAR:\n{json.dumps(items, ensure_ascii=False)}\n\nKOD:\n{code}"
    )


def _text(value, limit: int = 400) -> str | None:
    """Boş olmayan metni kırpıp döndürür; metin değilse ya da boşsa None."""
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _clean_review(record: dict) -> tuple[dict | None, bool]:
    """Tek açıklama kaydı -> ({neden, duzeltme, karar, gerekce} | None, karar atıldı mı).

    Karar yalnızca bilinen üç değerden biriyse ve (onayla dışında) gerekçesi varsa kabul edilir; aksi halde
    karar atılır ama açıklama (neden + düzeltme) eski kurala göre yine geçerli olabilir.
    """
    raw_verdict, reason = record.get("karar"), _text(record.get("gerekce"))
    verdict = raw_verdict if raw_verdict in VERDICTS else None
    if verdict in ("yanlis_pozitif", "baglama_bagli") and (reason is None or len(reason) < MIN_REASON_CHARS):
        verdict = None  # gerekçesiz eleme/koşul kabul edilmez: sessizce bir bulguyu yok saymak istemiyoruz
    dropped = raw_verdict is not None and verdict is None
    reason_text, fix_text = _text(record.get("neden")), _text(record.get("duzeltme"))
    if verdict is not None:
        return {"neden": reason_text or reason or "", "duzeltme": fix_text or "", "karar": verdict,
                "gerekce": reason or ""}, dropped
    if reason_text and isinstance(record.get("duzeltme"), str):
        return {"neden": reason_text, "duzeltme": fix_text or "", "karar": None, "gerekce": ""}, dropped
    return None, dropped


def _clean_explanations(records, valid: set) -> tuple[dict, int, int]:
    """Yalnızca motorun gerçekten bulduğu (kural, satır) çiftleri için açıklama/karar kabul edilir.

    (kayıtlar, atılan kayıt sayısı, atılan karar sayısı) döndürür.
    """
    out, rejected, dropped_verdicts = {}, 0, 0
    for record in records if isinstance(records, list) else []:
        pair = (record.get("rule"), record.get("line")) if isinstance(record, dict) else None
        cleaned, dropped = _clean_review(record) if pair in valid else (None, False)
        dropped_verdicts += dropped
        if cleaned is None:
            rejected += 1
            continue
        out[f"{pair[0]}:{pair[1]}"] = cleaned
    return out, rejected, dropped_verdicts


def _clean_fix(raw: dict, code: str, config: Config | None) -> dict:
    """Düzeltilmiş kodu statik olarak doğrular. Kod yoksa 'kod' None olur (açıklamalar yine kullanılır)."""
    fixed_text = raw.get("kod")
    if not isinstance(fixed_text, str) or not fixed_text.strip():
        return {"kod": None, "degisiklikler": [], "dogrulama": None}
    fixed = llm.strip_code_fence(fixed_text) + "\n"
    changes = [str(c)[:200] for c in raw.get("degisiklikler", []) if isinstance(c, str)][:8]
    return {"kod": fixed, "degisiklikler": changes, "dogrulama": llm.verify_fix(code, fixed, config)}


def _clean_function(name, before: str, after: str | None) -> str | None:
    """LLM'in önerdiği fonksiyon adı iki sürümde de gerçekten varsa kabul edilir."""
    if not isinstance(name, str):
        return None
    pool = common_functions(before, after) if after else top_level_functions(before)
    return name if name in pool else None


def review_and_fix(code: str, findings: list[dict], settings: llm.Settings, *, live: bool = True,
                   transport=llm._http, config: Config | None = None) -> llm.Outcome:
    """TEK LLM çağrısı: bulgu açıklamaları + düzeltilmiş kod + test girdileri. Her parça ayrı doğrulanır."""
    llm._check_size(code)
    chosen = [f for f in findings if f["category"] != "AI Pattern"][:llm.MAX_FINDINGS_PER_CALL]
    if not chosen:
        raise llm.LLMError("Düzeltilecek bulgu yok.")
    user = _prompt(code, _rule_guidance(chosen))
    valid = {(f["rule"], f["line"]) for f in chosen}

    def validate(raw) -> dict:
        if not isinstance(raw, dict):
            raise llm.LLMError("Model beklenen JSON nesnesini döndürmedi.")
        notes, rejected, dropped_verdicts = _clean_explanations(raw.get("aciklamalar"), valid)
        extras, rejected_extras = llm.clean_candidates(raw.get("ek_sorunlar"), code, findings, relocate=True)
        fix = _clean_fix(raw, code, config)
        unchanged = fix["kod"] is not None and fix["kod"].strip() == code.strip()
        if unchanged:  # değişiklik önerilmedi: "bulgular azalmadı" diye reddedilecek sahte bir öneri göstermeyiz
            fix = {"kod": None, "degisiklikler": [], "dogrulama": None}
        cases, rejected_cases = sanitize_cases(raw.get("test_girdileri"))
        if not notes and not extras and fix["kod"] is None:
            raise llm.LLMError("Model kullanılabilir bir açıklama ya da düzeltme döndürmedi.")
        return {"aciklamalar": notes, "reddedilen": rejected, "reddedilen_karar": dropped_verdicts,
                "ek_sorunlar": extras[:MAX_EXTRA_ISSUES], "reddedilen_ek": rejected_extras,
                "degisiklik_yok": unchanged, **fix, "test_girdileri": cases, "reddedilen_test": rejected_cases,
                "fonksiyon": _clean_function(raw.get("fonksiyon"), code, fix["kod"])}

    return llm._run_task(settings, HYBRID_TASK, (llm._SYSTEM, user), validate, live=live, transport=transport)


# --------------------------------------------------------------------------- dinamik sonucun yorumu

MIN_MEANINGFUL_MS = 0.05   # bundan küçük süre farkları ölçüm gürültüsüdür; "yavaşladı" diye yorumlanmaz


def function_speeds(result: dict) -> list[dict]:
    """Fonksiyon başına hız karşılaştırması (her fonksiyonun Önce'de en yavaş kalan girdisi için)."""
    rows = []
    for name, data in (result.get("by_function") or {}).items():
        single = {"ok": True, "before": data["before"], "after": data["after"]}
        info = summarize(single)
        gap = abs(data["before"]["median_ms"] - data["after"]["median_ms"])
        rows.append({"function": name, "case": data.get("case"), "speedup": info["speedup"],
                     "faster": "daha hızlı" in info["time_verdict"] and gap >= MIN_MEANINGFUL_MS,
                     "slower": "yavaş" in info["time_verdict"] and gap >= MIN_MEANINGFUL_MS,
                     "verdict": info["time_verdict"],
                     "before_ms": data["before"]["median_ms"], "after_ms": data["after"]["median_ms"]})
    return rows


def dynamic_state(dyn: dict | None) -> dict:
    """Dinamik katmanın durumu: none | unmeasurable | same | different | error (+ ayrıntılar)."""
    if not dyn:
        return {"kind": "none"}
    if dyn.get("reason"):
        return {"kind": "unmeasurable", "reason": dyn["reason"]}
    result = dyn.get("result") or {}
    if result.get("ok"):
        functions = function_speeds(result)
        return {"kind": "same", "cases_total": result.get("cases_total", 1), "summary": summarize(result),
                "headline": result.get("headline_case"), "functions": functions,
                "slower_functions": [f["function"] for f in functions if f["slower"]]}
    if "before_outcome" in result:
        cases = result.get("cases") or []
        return {"kind": "different", "total": len(cases) or 1,
                "bad": sum(1 for c in cases if not c.get("same")) or 1}
    return {"kind": "error", "error": result.get("error", "Ölçüm başarısız")}


def _minor_introduced(check: dict | None) -> int:
    """Statik doğrulamayı geçen bir düzeltmenin doğurduğu (düşük önemli) yeni bulgu sayısı."""
    effect = (check or {}).get("effect") or {}
    return effect.get("introduced", 0) if check and check["verified"] else 0


def _is_slower(state: dict) -> bool:
    """Genel ölçüm ya da herhangi bir fonksiyon Önce sürümünden anlamlı biçimde yavaşsa True."""
    if state["kind"] != "same":
        return False
    return "yavaş" in state["summary"]["time_verdict"] or bool(state.get("slower_functions"))


def performance_functions(code: str, findings: list[dict]) -> list[str]:
    """Önce sürümünde Performans bulgusu bulunan üst düzey fonksiyonların adları."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    spans = [(n.name, n.lineno, getattr(n, "end_lineno", n.lineno)) for n in tree.body if isinstance(n, ast.FunctionDef)]
    names = {name for f in findings if f["category"] == "Performance"
             for name, start, end in spans if start <= f["line"] <= end}
    return sorted(names)


def unproven_speedups(state: dict, perf_functions, changed: list[str]) -> list[str]:
    """Statik olarak "performans bulgusu çözüldü" görünen ama ölçümde belirgin hız kazancı olmayan fonksiyonlar."""
    if state["kind"] != "same":
        return []
    speeds = {f["function"]: f for f in state.get("functions", [])}
    return [name for name in perf_functions if name in changed and name in speeds and not speeds[name]["faster"]]


def _speed_text(summary: dict) -> str:
    speed = f"{summary['speedup']:.2f}×" if summary.get("speedup") else "ölçülemedi"
    return f"Hız oranı {speed} ({summary['time_verdict']}); bellek: {summary['memory_verdict']}."


def _static_row(check: dict | None) -> dict:
    layer = "Statik (AST)"
    if not check:
        return {"layer": layer, "status": "na", "text": "Sonra sürümü yok; karşılaştırılacak bir şey yok."}
    passed = sum(1 for c in check["checks"] if c["gecti"])
    if check["verified"]:
        effect = check["effect"]
        return {"layer": layer, "status": "ok",
                "text": f"{passed}/{len(check['checks'])} kontrol geçti: {effect['resolved']} bulgu çözüldü, "
                        f"{effect['introduced']} yeni bulgu."}
    failed = ", ".join(c["kontrol"] for c in check["checks"] if not c["gecti"])
    return {"layer": layer, "status": "fail", "text": f"Başarısız kontrol: {failed}."}


def _coverage_row(coverage: dict | None) -> list[dict]:
    if not coverage or not (coverage["changed"] or coverage["indirect"]):
        return []
    status = "ok" if coverage["complete"] and not coverage["indirect"] else "warn"
    text = coverage_text(coverage)
    if coverage["indirect"]:
        text += f" Doğrudan sınanamayan değişiklik: {', '.join(coverage['indirect'])}."
    return [{"layer": "Dinamik: test kapsamı", "status": status, "text": text}]


def _dynamic_rows(state: dict) -> list[dict]:
    names = ("Dinamik: çıktı eşitliği", "Dinamik: hız ve bellek")
    kind = state["kind"]
    if kind == "same":
        total = state["cases_total"]
        speed_status = {"daha hızlı": "ok", "yavaş": "warn"}
        verdict = state["summary"]["time_verdict"]
        status = next((s for key, s in speed_status.items() if key in verdict), "na")
        return [{"layer": names[0], "status": "ok",
                 "text": f"Denenen {total} girdinin {total}'inde çıktı, ekrana yazılanlar ve girdilerin son hali aynı "
                         "(yalnızca bu girdiler için geçerli)."},
                {"layer": names[1], "status": status, "text": _speed_text(state["summary"])}]
    if kind == "different":
        first = f"{state['bad']}/{state['total']} girdide çıktı farklı: düzeltme davranışı değiştirmiş."
        return [{"layer": names[0], "status": "fail", "text": first},
                {"layer": names[1], "status": "na", "text": "Çıktılar farklı olduğu için ölçülmedi."}]
    text = {"none": "Henüz ölçülmedi.", "unmeasurable": f"Ölçülemez: {state.get('reason', '')}",
            "error": f"Ölçüm yapılamadı: {state.get('error', '')}"}[kind]
    return [{"layer": names[0], "status": "warn" if kind == "error" else "na", "text": text},
            {"layer": names[1], "status": "na", "text": "—"}]


def _decide(check: dict | None, state: dict, allow_behavior_change: bool = False, coverage: dict | None = None,
            unproven: list[str] | None = None) -> tuple[str, str]:
    """(karar, başlık). Karar: bekliyor | red | kismen | kabul | kabul_uyari."""
    if not check:
        return "bekliyor", "Sonra sürümü bekleniyor"
    if not check["verified"]:
        return "red", "Reddedildi: statik doğrulama başarısız"
    if state["kind"] == "different":
        if allow_behavior_change:
            return "kabul_uyari", "Kabul (uyarıyla): çıktı değişti; bu değişiklik bilerek kabul edildi"
        return "red", "Reddedildi: çıktılar farklı, düzeltme davranışı değiştirmiş"
    if state["kind"] != "same":
        return "kismen", "Kısmen doğrulandı: statik geçti, davranış henüz ölçülmedi"
    if _is_slower(state):
        slow = state.get("slower_functions") or []
        where = f" ({', '.join(slow)})" if slow else ""
        return "kabul_uyari", f"Kabul edilebilir ama dikkat: Sonra sürümü daha yavaş{where}"
    if coverage and coverage["untested"]:
        return "kismen", ("Kısmen doğrulandı: değişen şu fonksiyonlar için test girdisi yok: "
                          + ", ".join(coverage["untested"]))
    if unproven:
        return "kabul_uyari", ("Kabul edilebilir ama dikkat: statik bulgu çözüldü, fakat ölçümde hız kazancı yok: "
                               + ", ".join(unproven))
    if coverage and coverage["indirect"]:
        return "kabul_uyari", ("Kabul edilebilir ama dikkat: şu değişiklikler doğrudan sınanamadı: "
                               + ", ".join(coverage["indirect"]))
    if _minor_introduced(check):
        return "kabul_uyari", "Kabul edilebilir ama dikkat: yeni bir YZ göstergesi doğdu (bulgu sayılmaz), çıktı ve hız tamam"
    total = state["cases_total"]
    return "kabul", f"Kabul edilebilir: statik doğrulama geçti, {total}/{total} girdide davranış aynı"


def _coverage_of(dyn: dict | None, sources: tuple[str, str] | None) -> dict | None:
    """Ölçümde kullanılan girdilere göre test kapsamı (kaynaklar bilinmiyorsa None)."""
    result = (dyn or {}).get("result") or {}
    if not sources or not result.get("cases"):
        return None
    cases = [{"function": c.get("function")} for c in result["cases"] if c.get("function")]
    return test_coverage(sources[0], sources[1], cases)


def overall_verdict(check: dict | None, dyn: dict | None, allow_behavior_change: bool = False, *,
                    sources: tuple[str, str] | None = None, perf_functions=()) -> dict:
    """Üç katmanın birleşik kararı: {"decision", "title", "rows"}.

    `allow_behavior_change`: güvenlik düzeltmeleri (ör. md5 -> sha256) çıktıyı kaçınılmaz olarak değiştirir;
    kullanıcı bunu bilerek kabul ederse "çıktılar farklı" durumu red yerine uyarılı kabul sayılır."""
    state = dynamic_state(dyn)
    coverage = _coverage_of(dyn, sources) if state["kind"] == "same" else None
    unproven = unproven_speedups(state, perf_functions, coverage["changed"] if coverage else [])
    decision, title = _decide(check, state, allow_behavior_change, coverage, unproven)
    rows = [_static_row(check), *_dynamic_rows(state), *_coverage_row(coverage)]
    if unproven:
        rows.append({"layer": "Dinamik: hız kanıtı", "status": "warn",
                     "text": "Statik kural bu fonksiyonlarda bulguyu çözülmüş saydı ama ölçümde belirgin hız kazancı "
                             "yok: " + ", ".join(unproven) + ". Kural kandırılmış olabilir."})
    return {"decision": decision, "title": title, "rows": rows, "coverage": coverage, "unproven": unproven}


# --------------------------------------------------------------------------- reddedilen düzeltmeyi geri bildirimle yeniden düzelttirme

MAX_REFINE_ROUNDS = 3
_MAX_FEEDBACK_ITEMS = 12


def _static_notes(after: str, check: dict | None) -> list[str]:
    if not check:
        return []
    effect = check.get("effect") or {}
    notes = [f"Düzeltme yeni bulgu doğurdu: {r['rule']} ({r['message']})" for r in effect.get("introduced_rules", [])]
    if check["verified"]:
        return notes
    notes = [f"Statik kontrol başarısız: {c['kontrol']}. {c['ayrinti']}" for c in check["checks"] if not c["gecti"]] + notes
    result = analyze(after)
    if result.valid:
        notes += [f"Hâlâ duran bulgu: {f['rule']} satır {f['line']}: {f['message']}"
                  for f in result.issues if f["category"] != "AI Pattern"]
    return notes


def _dynamic_notes(dyn: dict | None, unproven: list[str] | None = None) -> list[str]:
    state = dynamic_state(dyn)
    notes: list[str] = []
    if state["kind"] == "different":
        cases = ((dyn or {}).get("result") or {}).get("cases") or []
        for case in [c for c in cases if not c.get("same")][:3]:
            reasons = ", ".join(case.get("diff") or []) or "çıktı"
            where = f"{case.get('function')}, " if case.get("function") else ""
            notes.append(f"Davranış farklı ({where}girdi '{case.get('label')}', fark: {reasons}): Önce sürümü "
                         f"{case.get('before_outcome', '')[:120]} verdi, senin sürümün {case.get('after_outcome', '')[:120]} verdi. "
                         "Dönüş değeri, print çıktısı ve girdi listesinin yerinde değişmesi de birebir korunmalı.")
    if _is_slower(state):
        slow = state.get("slower_functions") or []
        notes.append("Sonra sürümü Önce sürümünden daha yavaş çıktı"
                     + (f" ({', '.join(slow)})" if slow else "") + "; daha verimli bir çözüm gerekli. "
                     + _speed_text(state["summary"]))
    if unproven:
        notes.append("Statik kural şu fonksiyonlarda bulguyu çözülmüş saydı ama ölçümde hız kazancı yok (döngüyü "
                     "comprehension'a taşımak algoritmik maliyeti azaltmaz): " + ", ".join(unproven)
                     + ". Gerçekten daha az işlem yapan bir algoritma (set/dict/Counter, tek geçiş) kullan.")
    return notes


def rejection_feedback(before: str, after: str, check: dict | None, dyn: dict | None, perf_functions=()) -> list[str]:
    """Reddedilen (ya da yavaş) düzeltme için LLM'e anlatılacak somut, kısa nedenler."""
    state = dynamic_state(dyn)
    coverage = _coverage_of(dyn, (before, after)) if state["kind"] == "same" else None
    unproven = unproven_speedups(state, perf_functions, coverage["changed"] if coverage else [])
    return (_static_notes(after, check) + _dynamic_notes(dyn, unproven))[:_MAX_FEEDBACK_ITEMS]


def _refine_prompt(code: str, previous: str, items: list[dict], feedback: list[str]) -> str:
    return (
        llm.BEHAVIOR_RULE +
        "Daha önce önerdiğin düzeltme doğrulamadan geçemedi. Aşağıda ORİJİNAL kod, senin önceki denemen ve "
        "doğrulamanın bulduğu somut sorunlar var. Sorunları gidererek kodu yeniden düzelt.\n"
        "Kurallar: ORİJİNAL kodun davranışı birebir korunmalı (aynı girdiye aynı çıktı, aynı veri tipi ve değer); "
        "fonksiyon adlarını, imzalarını ve dönüş biçimini değiştirme; yeni bağımlılık ekleme; gereksiz yeniden yazma "
        "yapma; yeni bulgu doğurma. Bir bulgu, çıktıyı değiştirmeden giderilemiyorsa (ör. farklı bir özet algoritması "
        "çıktıyı değiştirir) onu OLDUĞU GİBİ BIRAK ve nedenini 'degisiklikler' içinde söyle; diğer bulguları gider. "
        "Hız hedefi: çözüm Önce sürümünden belirgin biçimde hızlı olmalı; iç içe döngüleri azaltmak için "
        "set/dict/Counter kullan ve yeni iç içe blok açma.\n"
        'JSON biçimi: {"kod": "tam düzeltilmiş kod", "degisiklikler": ["kısa Türkçe madde"]}\n\n'
        f"ORİJİNAL BULGULAR:\n{json.dumps(items, ensure_ascii=False)}\n\n"
        f"DOĞRULAMANIN BULDUĞU SORUNLAR:\n{json.dumps(feedback, ensure_ascii=False)}\n\n"
        f"ORİJİNAL KOD:\n{code}\n\nÖNCEKİ DENEMEN:\n{previous}"
    )


def refine_fix(code: str, previous: str, findings: list[dict], feedback: list[str], settings: llm.Settings, *,
               transport=llm._http, config: Config | None = None) -> llm.Outcome:
    """Reddedilen düzeltmeyi, doğrulamanın bulduğu nedenlerle birlikte LLM'e geri verip yenisini ister.

    Yeni öneri yine statik olarak doğrulanır (`dogrulama`). Her zaman canlı çağrı yapar (önbellek kullanılmaz)."""
    llm._check_size(code)
    chosen = [f for f in findings if f["category"] != "AI Pattern"][:llm.MAX_FINDINGS_PER_CALL]
    if not chosen or not feedback:
        raise llm.LLMError("Yeniden düzeltme için bulgu ya da geri bildirim yok.")
    user = _refine_prompt(code, previous, _rule_guidance(chosen), feedback)

    def validate(raw) -> dict:
        if not isinstance(raw, dict):
            raise llm.LLMError("Model beklenen JSON nesnesini döndürmedi.")
        fix = _clean_fix(raw, code, config)
        if fix["kod"] is None:
            raise llm.LLMError("Model düzeltilmiş kod döndürmedi.")
        if fix["kod"].strip() == previous.strip():
            raise llm.LLMError("Model önceki öneriyi değiştirmedi.")
        return fix

    return llm._run_task(settings, "refine", (llm._SYSTEM, user), validate, live=True, transport=transport)


def auto_refine(code: str, after: str, findings: list[dict], settings: llm.Settings, *, run_dynamic=None,
                rounds: int = MAX_REFINE_ROUNDS, allow_behavior_change: bool = False,
                transport=llm._http, config: Config | None = None) -> dict:
    """Reddedilen (ya da daha yavaş) düzeltmeyi, kabul edilene ya da tur sınırına kadar geri bildirimle yeniden düzelttirir.

    `run_dynamic(after) -> dyn | None`: statik kontrolü geçen her aday için çalıştırılır (yoksa yalnızca statik döngü).
    Dönen sözlük: kod, check, dyn, verdict, history (her tur: karar, başlık, nedenler, varsa hata)."""
    rounds = max(0, min(int(rounds), MAX_REFINE_ROUNDS))
    perf = performance_functions(code, findings)
    history: list[dict] = []
    current = after
    for turn in range(rounds + 1):
        check = llm.verify_fix(code, current, config)
        dyn = run_dynamic(current) if run_dynamic and check["verified"] else None
        state = dynamic_state(dyn)
        verdict = overall_verdict(check, dyn, allow_behavior_change, sources=(code, current), perf_functions=perf)
        entry = {"tur": turn, "karar": verdict["decision"], "baslik": verdict["title"],
                 "nedenler": rejection_feedback(code, current, check, dyn, perf)}
        history.append(entry)
        needs_work = (verdict["decision"] == "red" or _is_slower(state) or bool(_minor_introduced(check))
                      or bool(verdict["unproven"]))
        if turn == rounds or not needs_work:
            break
        try:
            current_next = refine_fix(code, current, findings, entry["nedenler"], settings,
                                      transport=transport, config=config).data["kod"]
        except llm.LLMError as exc:
            entry["hata"] = str(exc)
            break
        current = current_next
    return {"kod": current, "check": check, "dyn": dyn, "verdict": verdict, "history": history}


# --------------------------------------------------------------------------- bulgu kartları (rozetler)

def _resolved_flags(findings: list[dict], after_findings: list[dict] | None) -> list[bool | None]:
    """Her bulgu Sonra sürümünde çözüldü mü? (Kural başına azalan adet kadar ilk bulgular çözülmüş sayılır.)"""
    if after_findings is None:
        return [None] * len(findings)
    before_count = Counter(f["rule"] for f in findings)
    after_count = Counter(f["rule"] for f in after_findings)
    quota = {rule: max(0, n - after_count[rule]) for rule, n in before_count.items()}
    flags: list[bool | None] = []
    for f in findings:
        resolved = quota[f["rule"]] > 0
        quota[f["rule"]] -= 1 if resolved else 0
        flags.append(resolved)
    return flags


def _static_badges(f: dict, resolved: bool | None) -> list[dict]:
    extra = f" · teyit: {', '.join(f['corroborated_by'])}" if f.get("corroborated_by") else ""
    badges = [{"layer": "Statik", "status": "ok", "text": f"{f.get('source', 'AST')} kuralı {f['rule']}{extra}"}]
    if resolved is True:
        badges.append({"layer": "Statik (yeniden analiz)", "status": "ok", "text": "Sonra sürümünde artık yok."})
    elif resolved is False:
        badges.append({"layer": "Statik (yeniden analiz)", "status": "warn", "text": "Sonra sürümünde hâlâ duruyor."})
    return badges


def _llm_badges(note: dict | None, llm_ran: bool) -> list[dict]:
    verdict = (note or {}).get("karar")
    if verdict == "onayla":
        return [{"layer": "LLM", "status": "ok", "text": "Doğruladı: bu kodda gerçek bir sorun."}]
    if verdict == "yanlis_pozitif":
        return [{"layer": "LLM", "status": "na", "text": "Elendi (yanlış pozitif): " + note["gerekce"]}]
    if verdict == "baglama_bagli":
        return [{"layer": "LLM", "status": "warn", "text": "Bağlama bağlı: " + note["gerekce"]}]
    if note:
        return [{"layer": "LLM", "status": "ok", "text": "Açıklama üretildi; kural ve satır statik bulguyla eşleşti."}]
    if llm_ran:
        return [{"layer": "LLM", "status": "na", "text": "Bu bulgu için açıklama gelmedi."}]
    return []


def _dynamic_badges(f: dict, resolved: bool | None, state: dict) -> list[dict]:
    if resolved is not True:
        return []
    kind = state["kind"]
    if kind == "same":
        summary, total = state["summary"], state["cases_total"]
        if f["category"] == "Performance":
            if "daha hızlı" in summary["time_verdict"]:
                return [{"layer": "Dinamik", "status": "ok", "text": f"{summary['speedup']:.2f}× daha hızlı (ölçüldü)."}]
            return [{"layer": "Dinamik", "status": "warn", "text": "Hızda belirgin bir iyileşme ölçülemedi."}]
        return [{"layer": "Dinamik", "status": "ok", "text": f"Davranış korundu ({total}/{total} girdide çıktı aynı)."}]
    if kind == "different":
        return [{"layer": "Dinamik", "status": "fail", "text": "Çıktı değişti: bu düzeltme reddedilmeli."}]
    return [{"layer": "Dinamik", "status": "na", "text": "Ölçülmedi ya da ölçülemedi."}]


def build_cards(findings: list[dict], explanations: dict | None, after_findings: list[dict] | None,
                dyn: dict | None, *, llm_ran: bool = False) -> list[dict]:
    """Her bulgu için {finding, resolved, badges, note, verdict, risky_elimination}. Rozetler hangi katmanın neyi doğruladığını söyler.

    `verdict`: LLM'in kararı (onayla / yanlis_pozitif / baglama_bagli) ya da karar verilmediyse None.
    Elenen bulgu kartlardan silinmez; `risky_elimination`, yüksek önemli bir bulgunun elendiğini işaretler.
    """
    state = dynamic_state(dyn)
    flags = _resolved_flags(findings, after_findings)
    cards = []
    for f, resolved in zip(findings, flags):
        note = (explanations or {}).get(f"{f['rule']}:{f['line']}")
        badges = [*_static_badges(f, resolved), *_llm_badges(note, llm_ran), *_dynamic_badges(f, resolved, state)]
        verdict = (note or {}).get("karar")
        cards.append({"finding": f, "resolved": resolved, "badges": badges, "note": note, "verdict": verdict,
                      "risky_elimination": verdict == "yanlis_pozitif" and f["severity"] in RISKY_SEVERITIES})
    return cards


def card_marker(card: dict) -> str:
    """Kart başlığı için kısa işaret: ✗ (reddedildi), ⊘ (LLM eledi), ✓ (çözüldü), • (açık)."""
    if any(b["status"] == "fail" for b in card["badges"]):
        return "✗"
    if card.get("verdict") == "yanlis_pozitif":
        return "⊘"
    return "✓" if card["resolved"] else "•"


def active_findings(cards: list[dict]) -> list[dict]:
    """LLM'in elemediği bulgular (düzeltilecek olanlar). Elenenler silinmez, yalnızca bu listeden çıkar."""
    return [c["finding"] for c in cards if c["verdict"] != "yanlis_pozitif"]


def verdict_summary(cards: list[dict], nonblank_lines: int) -> dict:
    """Karar sayıları ve elemenin skora etkisi. AST skoru değişmez; 'LLM sonrası' skor ayrıca verilir.

    Skor, `metrics.quality_score` ile aynı formülle hesaplanır (tek doğruluk kaynağı).
    """
    counts = Counter(c["verdict"] or "karar_yok" for c in cards)

    def score(findings: list[dict]) -> int:
        objects = [Finding(**{**f, "corroborated_by": tuple(f.get("corroborated_by", ()))}) for f in findings]
        return quality_score(objects, nonblank_lines)

    return {
        "toplam": len(cards), "onayla": counts["onayla"], "yanlis_pozitif": counts["yanlis_pozitif"],
        "baglama_bagli": counts["baglama_bagli"], "karar_yok": counts["karar_yok"],
        "riskli_eleme": sum(c["risky_elimination"] for c in cards),
        "skor_ast": score([c["finding"] for c in cards]), "skor_llm_sonrasi": score(active_findings(cards)),
    }


def after_findings_of(after: str, config: Config | None = None) -> list[dict] | None:
    """Sonra sürümünün bulguları; kod geçersizse None."""
    result = analyze(after, config)
    return result.issues if result.valid else None


def hybrid_report(code: str, after: str | None, cards: list[dict], check: dict | None, dyn: dict | None,
                  meta: dict | None = None, extras: list[dict] | None = None, nonblank_lines: int | None = None) -> dict:
    """Üç katmanın sonucunu tek JSON'da toplar (teknik rapor ve sunum için)."""
    nonblank = nonblank_lines if nonblank_lines is not None else sum(bool(x.strip()) for x in code.splitlines())
    llm_ran = any(c["note"] for c in cards) or bool(extras)
    return {
        "layers": "statik (AST) + LLM + dinamik (çalıştırma)",
        "llm": meta or {},
        "before": code,
        "after": after,
        "static_verification": check,
        "dynamic": (dyn or {}).get("result") if dyn and not dyn.get("reason") else dyn,
        "verdict": overall_verdict(check, dyn, sources=(code, after) if after else None,
                                   perf_functions=performance_functions(code, [c["finding"] for c in cards])),
        "llm_review": {"ran": llm_ran, "summary": verdict_summary(cards, nonblank) if cards else None,
                       "extra_issues": extras or []},
        "findings": [{**c["finding"], "resolved_in_after": c["resolved"], "badges": c["badges"],
                      "llm_note": c["note"], "llm_verdict": c["verdict"]} for c in cards],
        "limits": "Dinamik ölçüm yalnızca verilen test girdileri için geçerlidir; LLM çıktısı tek başına kanıt değildir. "
                  "Elenen (yanlış pozitif) bulgular silinmez; eleme LLM görüşüdür.",
    }
