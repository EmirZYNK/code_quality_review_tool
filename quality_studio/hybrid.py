"""Hibrit birleştirme: statik (AST), LLM ve dinamik (Docker/yerel) sonuçlarını tek kayıtta toplar.

Zincir: LLM önerir -> AST doğrular -> çalıştırma ölçer. Bu modül üç katmanın sonucunu birleştirir:

  * `review_and_fix`: TEK LLM çağrısı (açıklama + düzeltme + test girdisi önerisi); çıktının tamamı doğrulanır.
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
from .rules import RULES

HYBRID_TASK = "hybrid"


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
    shown = [{"etiket": c["label"], "args": c["args"], "kwargs": c["kwargs"]} for c in cases]
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


# --------------------------------------------------------------------------- tek LLM çağrısı

def _rule_guidance(findings: list[dict]) -> list[dict]:
    """LLM'e bulgularla birlikte kuralın resmi düzeltme önerisini de verir (kurallara uysun diye)."""
    items = []
    for f in findings:
        meta = RULES.get(f["rule"])
        items.append({"rule": f["rule"], "line": f["line"], "message": f["message"],
                      "kural_onerisi": meta.fix if meta else ""})
    return items


def _prompt(code: str, items: list[dict]) -> str:
    return (
        llm.BEHAVIOR_RULE +
        "Aşağıda bir Python kodu ve statik analizin bulguları var. TEK yanıtta üç iş yap:\n"
        "1) Her bulgu için bu koda özgü kısa açıklama ver (neden, duzeltme: 1-2 cümle).\n"
        "2) Kodu bulguları giderecek şekilde düzelt. Kurallar: davranışı koru, fonksiyon adlarını ve imzalarını "
        "değiştirme, her bulgunun 'kural_onerisi' alanına uy, gereksiz yeniden yazma yapma, yeni bağımlılık ekleme "
        "(standart kütüphane serbest). Hız önemli: bulguyla doğrudan ilgili olmasa bile, çıktıyı BİREBİR (sıra ve "
        "tekrarlar dahil) koruyarak yapılabiliyorsa belirgin algoritmik verimsizlikleri (iç içe döngüyle arama, "
        "döngüde .count(), döngüde liste büyütme) en verimli yöntemle (set/dict/Counter, comprehension) yaz. "
        "Düzeltmen yeni bir bulgu doğurmasın (ör. daha derin iç içe blok açma).\n"
        "3) Düzeltmenin davranışını sınamak için test girdileri üret. 'fonksiyon': sınanacak üst düzey genel "
        "fonksiyonun adı (uygun fonksiyon yoksa null). 'test_girdileri': en fazla 6 kayıt; bir sınır durumu "
        "(boş/sıfır), tipik bir durum ve en az bir orta büyüklükte girdi (ör. 300 elemanlı liste) olsun ki hız farkı "
        "görünsün. Girdiler JSON ile yazılabilmeli (args dizi, kwargs nesne). Doğru çıktıyı bilmen gerekmez. "
        "Dosya, ağ ya da uzun süren girdi verme.\n"
        'JSON biçimi: {"aciklamalar": [{"rule": "...", "line": 0, "neden": "...", "duzeltme": "..."}], '
        '"kod": "tam düzeltilmiş kod", "degisiklikler": ["kısa Türkçe madde"], "fonksiyon": "ad", '
        '"test_girdileri": [{"etiket": "kısa ad", "args": [], "kwargs": {}}]}\n'
        "Her bulgu için tam olarak bir açıklama kaydı ver; rule ve line değerlerini aynen kopyala.\n\n"
        f"BULGULAR:\n{json.dumps(items, ensure_ascii=False)}\n\nKOD:\n{code}"
    )


def _clean_explanations(records, valid: set) -> tuple[dict, int]:
    """Yalnızca motorun gerçekten bulduğu (kural, satır) çiftleri için açıklama kabul edilir."""
    out, rejected = {}, 0
    for record in records if isinstance(records, list) else []:
        pair = (record.get("rule"), record.get("line")) if isinstance(record, dict) else None
        if pair in valid and isinstance(record.get("neden"), str) and isinstance(record.get("duzeltme"), str):
            out[f"{pair[0]}:{pair[1]}"] = {"neden": record["neden"].strip()[:400],
                                           "duzeltme": record["duzeltme"].strip()[:400]}
        else:
            rejected += 1
    return out, rejected


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
        notes, rejected = _clean_explanations(raw.get("aciklamalar"), valid)
        fix = _clean_fix(raw, code, config)
        cases, rejected_cases = sanitize_cases(raw.get("test_girdileri"))
        if not notes and fix["kod"] is None:
            raise llm.LLMError("Model kullanılabilir bir açıklama ya da düzeltme döndürmedi.")
        return {"aciklamalar": notes, "reddedilen": rejected, **fix, "test_girdileri": cases,
                "reddedilen_test": rejected_cases, "fonksiyon": _clean_function(raw.get("fonksiyon"), code, fix["kod"])}

    return llm._run_task(settings, HYBRID_TASK, (llm._SYSTEM, user), validate, live=live, transport=transport)


# --------------------------------------------------------------------------- dinamik sonucun yorumu

def dynamic_state(dyn: dict | None) -> dict:
    """Dinamik katmanın durumu: none | unmeasurable | same | different | error (+ ayrıntılar)."""
    if not dyn:
        return {"kind": "none"}
    if dyn.get("reason"):
        return {"kind": "unmeasurable", "reason": dyn["reason"]}
    result = dyn.get("result") or {}
    if result.get("ok"):
        return {"kind": "same", "cases_total": result.get("cases_total", 1), "summary": summarize(result),
                "headline": result.get("headline_case")}
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
    return state["kind"] == "same" and "yavaş" in state["summary"]["time_verdict"]


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


def _dynamic_rows(state: dict) -> list[dict]:
    names = ("Dinamik: çıktı eşitliği", "Dinamik: hız ve bellek")
    kind = state["kind"]
    if kind == "same":
        total = state["cases_total"]
        speed_status = {"daha hızlı": "ok", "yavaş": "warn"}
        verdict = state["summary"]["time_verdict"]
        status = next((s for key, s in speed_status.items() if key in verdict), "na")
        return [{"layer": names[0], "status": "ok",
                 "text": f"Denenen {total} girdinin {total}'inde çıktı aynı (yalnızca bu girdiler için geçerli)."},
                {"layer": names[1], "status": status, "text": _speed_text(state["summary"])}]
    if kind == "different":
        first = f"{state['bad']}/{state['total']} girdide çıktı farklı: düzeltme davranışı değiştirmiş."
        return [{"layer": names[0], "status": "fail", "text": first},
                {"layer": names[1], "status": "na", "text": "Çıktılar farklı olduğu için ölçülmedi."}]
    text = {"none": "Henüz ölçülmedi.", "unmeasurable": f"Ölçülemez: {state.get('reason', '')}",
            "error": f"Ölçüm yapılamadı: {state.get('error', '')}"}[kind]
    return [{"layer": names[0], "status": "warn" if kind == "error" else "na", "text": text},
            {"layer": names[1], "status": "na", "text": "—"}]


def _decide(check: dict | None, state: dict, allow_behavior_change: bool = False) -> tuple[str, str]:
    """(karar, başlık). Karar: bekliyor | red | kismen | kabul | kabul_uyari."""
    if not check:
        return "bekliyor", "Sonra sürümü bekleniyor"
    if not check["verified"]:
        return "red", "Reddedildi: statik doğrulama başarısız"
    if state["kind"] == "different":
        if allow_behavior_change:
            return "kabul_uyari", "Kabul (uyarıyla): çıktı değişti; bu değişiklik bilerek kabul edildi"
        return "red", "Reddedildi: çıktılar farklı, düzeltme davranışı değiştirmiş"
    if _is_slower(state):
        return "kabul_uyari", "Kabul edilebilir ama dikkat: Sonra sürümü daha yavaş"
    minor = _minor_introduced(check)
    if state["kind"] == "same" and minor:
        return "kabul_uyari", f"Kabul edilebilir ama dikkat: {minor} küçük yeni bulgu doğdu, çıktı ve hız tamam"
    if state["kind"] == "same":
        total = state["cases_total"]
        return "kabul", f"Kabul edilebilir: statik doğrulama geçti, {total}/{total} girdide çıktı aynı"
    return "kismen", "Kısmen doğrulandı: statik geçti, davranış henüz ölçülmedi"


def overall_verdict(check: dict | None, dyn: dict | None, allow_behavior_change: bool = False) -> dict:
    """Üç katmanın birleşik kararı: {"decision", "title", "rows"}.

    `allow_behavior_change`: güvenlik düzeltmeleri (ör. md5 -> sha256) çıktıyı kaçınılmaz olarak değiştirir;
    kullanıcı bunu bilerek kabul ederse "çıktılar farklı" durumu red yerine uyarılı kabul sayılır."""
    state = dynamic_state(dyn)
    decision, title = _decide(check, state, allow_behavior_change)
    return {"decision": decision, "title": title, "rows": [_static_row(check), *_dynamic_rows(state)]}


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


def _dynamic_notes(dyn: dict | None) -> list[str]:
    state = dynamic_state(dyn)
    notes: list[str] = []
    if state["kind"] == "different":
        cases = ((dyn or {}).get("result") or {}).get("cases") or []
        for case in [c for c in cases if not c.get("same")][:3]:
            notes.append(f"Çıktı farklı, girdi '{case.get('label')}': Önce sürümü {case.get('before_outcome', '')[:120]} "
                         f"döndürdü, senin sürümün {case.get('after_outcome', '')[:120]} döndürdü.")
    if _is_slower(state):
        notes.append("Sonra sürümü Önce sürümünden daha yavaş çıktı; daha verimli bir çözüm gerekli. "
                     + _speed_text(state["summary"]))
    return notes


def rejection_feedback(before: str, after: str, check: dict | None, dyn: dict | None) -> list[str]:
    """Reddedilen (ya da yavaş) düzeltme için LLM'e anlatılacak somut, kısa nedenler."""
    return (_static_notes(after, check) + _dynamic_notes(dyn))[:_MAX_FEEDBACK_ITEMS]


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
    history: list[dict] = []
    current = after
    for turn in range(rounds + 1):
        check = llm.verify_fix(code, current, config)
        dyn = run_dynamic(current) if run_dynamic and check["verified"] else None
        state = dynamic_state(dyn)
        verdict = overall_verdict(check, dyn, allow_behavior_change)
        entry = {"tur": turn, "karar": verdict["decision"], "baslik": verdict["title"],
                 "nedenler": rejection_feedback(code, current, check, dyn)}
        history.append(entry)
        needs_work = verdict["decision"] == "red" or _is_slower(state) or bool(_minor_introduced(check))
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
    """Her bulgu için {finding, resolved, badges, note}. Rozetler hangi katmanın neyi doğruladığını söyler."""
    state = dynamic_state(dyn)
    flags = _resolved_flags(findings, after_findings)
    cards = []
    for f, resolved in zip(findings, flags):
        note = (explanations or {}).get(f"{f['rule']}:{f['line']}")
        badges = [*_static_badges(f, resolved), *_llm_badges(note, llm_ran), *_dynamic_badges(f, resolved, state)]
        cards.append({"finding": f, "resolved": resolved, "badges": badges, "note": note})
    return cards


def card_marker(card: dict) -> str:
    """Kart başlığı için kısa işaret: ✗ (reddedildi), ✓ (çözüldü), • (açık)."""
    if any(b["status"] == "fail" for b in card["badges"]):
        return "✗"
    return "✓" if card["resolved"] else "•"


def after_findings_of(after: str, config: Config | None = None) -> list[dict] | None:
    """Sonra sürümünün bulguları; kod geçersizse None."""
    result = analyze(after, config)
    return result.issues if result.valid else None


def hybrid_report(code: str, after: str | None, cards: list[dict], check: dict | None, dyn: dict | None,
                  meta: dict | None = None) -> dict:
    """Üç katmanın sonucunu tek JSON'da toplar (teknik rapor ve sunum için)."""
    return {
        "layers": "statik (AST) + LLM + dinamik (çalıştırma)",
        "llm": meta or {},
        "before": code,
        "after": after,
        "static_verification": check,
        "dynamic": (dyn or {}).get("result") if dyn and not dyn.get("reason") else dyn,
        "verdict": overall_verdict(check, dyn),
        "findings": [{**c["finding"], "resolved_in_after": c["resolved"], "badges": c["badges"],
                      "llm_note": c["note"]} for c in cards],
        "limits": "Dinamik ölçüm yalnızca verilen test girdileri için geçerlidir; LLM çıktısı tek başına kanıt değildir.",
    }
