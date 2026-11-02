# Changelog

## Unreleased — Adım 6: Hibrit sistem

### Added
- `quality_studio/hybrid.py`: üç katmanı (statik AST, LLM, dinamik ölçüm) birleştirir.
  - `review_and_fix`: **tek LLM çağrısı** ile bulgu açıklamaları + düzeltilmiş kod + fonksiyon adı + test girdileri; kural kayıtlarındaki
    resmi düzeltme önerileri isteme eklenir; her parça ayrı doğrulanır (uydurma kural/satır, geçersiz kod, olmayan fonksiyon, bozuk JSON atılır).
  - `build_cards`: her bulgu için **Statik / LLM / Dinamik rozetleri**; düzeltmeden sonra yeniden analizle "çözüldü" bilgisi.
  - `overall_verdict`: statik doğrulama + çıktı eşitliği + hız tek karara bağlanır (kabul / uyarılı kabul / kısmen / red / bekliyor).
  - `hybrid_report`: üç katmanın sonucunu tek JSON olarak indirir.
- `benchmark(..., cases=[...])`: **çoklu test girdisi**. Her girdi için çıktı eşitliği denetlenir, bir girdide fark varsa düzeltme reddedilir;
  girdi başına süre tablosu. Eski tek girdili kullanım aynen çalışır.
- Arayüz **3 sekmeye** indi: *Hibrit İnceleme* (tek sayfalık akış), *Proje ve Değerlendirme*, *Katmanlar ve Sunum Rehberi*.
  Aday öneriler ana akıştan çıkarıldı, "LLM ikinci görüş" bölümüne taşındı. Kural tabanlı hızlı yamalar korundu.
- Ölçülemeyen durumlar açıkça gösterilir (fonksiyonu olmayan script, ortak fonksiyon yok, tüm girdilerde aynı hata).
- Sonra sürümü düzenlenince statik doğrulama, rozetler ve sonuç kartı yeniden hesaplanır; kod ya da Sonra değişince eski ölçüm geçersiz sayılır.

### Changed
- Eski ayrı LLM düğmeleri (açıkla / düzelt) tek hibrit düğmeye birleşti (kota: 1 istek). `llm.propose_fix` ve `llm.explain_findings` duruyor.
- `benchmark()` iç yapısı sadeleştirildi (`_precheck`, `_execute`); `llm.strip_code_fence` eklendi.

### Tests
- `tests/test_hybrid.py` (33 test) ve yeniden yazılan arayüz duman testleri; toplam 180 test. Gerçek Gemini ve gerçek Docker çağrısı testlerde yapılmaz.

## Unreleased — Adım 5: LLM katmanı

### Added
- `quality_studio/llm.py`: sağlayıcıdan bağımsız LLM katmanı (Google Gemini ana seçenek; Groq ve OpenRouter ayardan seçilir).
  Üç görev: bulgu açıklaması, kaçırılan aday önerisi, düzeltme önerisi. Hepsi Türkçe ve kısa.
- Doğrulama zinciri: açıklamalar yalnızca gerçek (kural, satır) çiftleri için kabul edilir; adaylar satır + alıntı ile
  koda karşı kontrol edilir ve "AST ile örtüşüyor" / "doğrulanmadı" etiketi alır; düzeltmeler statik olarak doğrulanır
  (geçerli Python, adlar korundu, bulgu azaldı, yeni bulgu yok). Hız ve çıktı eşitliği Dinamik Analiz'de ölçülür.
- Kayıtlı yanıt önbelleği (`.llm_cache/`): internet kesilirse ya da kota dolarsa son başarılı yanıt "kayıtlı yanıt" etiketiyle gösterilir.
- "Modelleri listele": anahtarın kullanabildiği modelleri sağlayıcıdan sorar (model adları sık değiştiği için sabit yazılmadı).
- LLM sekmesinden "Dinamik Analiz'e gönder" düğmesi; LLM açıklamaları Kod İnceleme bulgu kartlarında da görünür.
- 17 yeni test (sahte ağ katmanıyla; gerçek sağlayıcı çağrısı testlerde yapılmaz).

- Sağlayıcı yoğunken (5xx) otomatik yeniden deneme (2 sn, 4 sn) ve yedek modellere geçiş (`gemini-2.5-flash`, `gemini-2.5-flash-lite`); gerçekte kullanılan model ekranda yazılır.

### Security
- API anahtarı yalnızca bellekte tutulur; URL'ye, istek gövdesine, önbelleğe ve `repr`'e girmez (HTTP başlığında gönderilir).
- Kod modele "veri" olarak verilir; modelin çıktısı her durumda doğrulanır. Gönderim için açık onay kutusu vardır.

## Unreleased — Adım 4: Dinamik Analiz sekmesi

### Added
- `benchmark.docker_status()`: Docker komutu / servisi / imajı için üç basamaklı durum kontrolü (arayüzde kontrol listesi).
- **Yerel yedek mod** (`BenchmarkOptions(mode="yerel")`): Docker yokken ayrı süreçte, boş ortamda, süre sınırıyla
  (Linux/macOS'ta ayrıca bellek/CPU sınırı) ölçüm. Gerçek izolasyon değildir; arayüzde açık uyarı ve onay kutusu var.
- `benchmark.summarize()`: hız ve bellek oranı, %10 gürültü eşiğiyle Türkçe karar cümlesi.
- Dinamik sekmede hazır örnek (döngüde liste birleştirme), iki modun güvenlik karşılaştırma tablosu ve
  "statik ve dinamik sonuç yan yana" tablosu.
- 12 yeni test (yerel mod, zaman aşımı, özet, Docker durumu, arayüz akışları).

- `benchmark.find_docker()`: `docker` PATH'te yoksa Docker Desktop'ın bilinen kurulum klasörlerinde arar
  (Windows'ta kurulumdan önce açılmış pencerelerde PATH güncel olmaz).

### Changed
- `benchmark.py` hata mesajları ve runner çıktıları Türkçeleştirildi; `runner.py` çalışma klasörünü argümandan alır.
- Başlıktaki Docker göstergesi artık yalnızca komutun varlığına değil servisin çalışmasına bakar.

## Önceki — Türkçe arayüz ve statik/dinamik sekmeleri

### Added
- `quality_studio/astview.py`: AST ağacı metni, bulgu-düğüm eşlemesi, kategori sayıları ve kural tablosu.
- Arayüzde **Statik Analiz** sekmesi (AST ağacı, tetiklenen kurallar, dağılım, kural kataloğu) ve **Dinamik Analiz**
  sekmesi (Docker ile önce/sonra ölçümü, statik/dinamik karşılaştırma tablosu); **LLM Açıklama** için yer tutucu sekme.
- Katman durumu satırı: statik hazır, dinamik (Docker bulundu mu), LLM bağlı değil.
- `models.py`: `CATEGORY_TR`, `SEVERITY_TR`, `CONFIDENCE_TR` görüntüleme sözlükleri (iç anahtarlar İngilizce kalır).
- `tests/test_astview.py` ve sahte `streamlit` ile `tests/test_app_smoke.py`.

### Changed
- 36 kuralın adı, "neden sorun?" ve "nasıl düzelir?" metinleri ile bulgu mesajları Türkçe ve kısa hale getirildi.
- Arayüz, CLI yardım metinleri, rapor (HTML/metin), yama açıklamaları, diff/kod görünümü ve hata mesajları Türkçeleştirildi.
- Docker benchmark paneli "Kod İnceleme" içinden çıkarılıp **Dinamik Analiz** sekmesine taşındı.
- `benchmark.py` hata mesajları henüz İngilizce (Dinamik katman adımında çevrilecek).

## Unreleased — dynamic before/after

### Added
- Side-by-side Before/After view (`quality_studio/diffview.py`): line diff, `resolved` / `new` finding chips,
  "only changed lines" toggle.
- Per-patch static effect (`impact.patch_effects`): score change shown next to every suggested patch, plus a live
  preview (score, resolved, new issues) for the currently selected patches before they are applied.
- Live analysis toggle in the Code Review tab: re-analyzes automatically when the editor content changes.

## 2.0.0 — rebuild

Replaces the earlier v3–v10 line (two apps, three READMEs) with one package and one UI.

### Added
- Rule registry (`quality_studio/rules/catalog.py`): id, category, severity, confidence, CWE mapping,
  threshold configuration (`Config`), `# noqa` / `# cqs: ignore[RULE]` suppression.
- New rules: SEC107–SEC111, CQ105–CQ108, PERF103, RED001–RED006, AIP001–AIP005, categories
  Code Smell and Redundancy.
- AI-generation indicators (probabilistic, severity `info`, excluded from the score).
- Optional ruff/bandit adapters that corroborate AST findings (`--external`).
- CLI with `text`/`json`/`sarif` output and exit codes (0 clean, 1 findings, 2 error); scans files,
  directories and ZIP archives.
- Statistical dataset comparison: Mann-Whitney U, Cliff's delta, bootstrap CI, Fisher exact test on
  syntax-error rates, Bonferroni adjustment. Rule evaluation against 139 hand-labeled snippets.
- ZIP project scan wired into the UI; technical report in `docs/TEKNIK_RAPOR.md`.

### Fixed
- Patch engine corrupted code containing non-ASCII text (ast byte offsets used as character indexes);
  line splitting now follows ast (`\n`, `\r\n`, `\r`).
- Benchmark timings were inflated ~10-16x by tracemalloc; time and memory are now separate rounds, and
  argument copying is outside the timer.
- Docker benchmark: image pull has its own timeout; containers are named and killed on timeout.
- SEC104 flags only dynamically built SQL; PERF102 no longer assumes `list`; SEC105 is word-based and covers
  attributes, annotated assignments, dict keys, defaults and keyword arguments.
- Nested-loop detection covers `for`/`while`/comprehensions and ignores linear traversals
  (`for cell in row`); long-function rule uses line span; one complexity definition; size-aware score
  that no longer saturates at 0.
- Dataset comparison reports (instead of silently dropping) syntax-error samples, validates CSV columns and
  lifts the 128 KB csv field limit.

### Removed
- Gemini/LLM code, `.env`, `ai_engine/`, legacy transformer, stub benchmark/sandbox modules, stale formatter,
  `advanced_app.py`, README_V7/V10, committed `__pycache__`.
