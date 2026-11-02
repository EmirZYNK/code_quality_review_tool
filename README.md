# Code Quality Studio 2.0

Python kaynak kodunu **çalıştırmadan** (yalnızca AST ile) analiz eden araç: güvenlik, performans, code smell, stil,
güvenilirlik, tekrar eden kod ve "yapay zekâ üretimi göstergeleri". Önce/sonra etki analizi, muhafazakâr yama önerileri,
ZIP/dizin taraması, istatistiksel veri seti karşılaştırması ve kural bazlı precision/recall ölçümü içerir.

> LLM katmanı **isteğe bağlıdır**: API anahtarı girilmezse araç yalnızca statik/dinamik katmanlarla çalışır
> (Hibrit İnceleme'de Sonra sürümünü elle yazabilir ya da kural tabanlı hızlı yamaları kullanabilirsiniz).
> Anahtar koda ya da dosyaya yazılmaz, yalnızca oturumda bellekte tutulur.
> Skor ve AI göstergeleri sezgiseldir; doğruluk, güvenlik veya yazarlık kanıtı değildir.

## Kurulum ve çalıştırma

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Arayüz tamamen Türkçedir. Üç sekme vardır:

| Sekme | Ne gösterir |
|---|---|
| **Hibrit İnceleme** | Tek sayfada beş bölüm: **1) Statik analiz** (AST bulguları, işaretli kod) → **2) LLM doğrulama ve açıklama** (her bulgu için onayla / yanlış pozitif / bağlama bağlı + gerekçe, koda özel açıklama, AST'nin kaçırdığı sorunlar *doğrulanmadı* etiketiyle) → **3) Önerileri uygula** (Önce \| Sonra) → **4) Dinamik analiz** (çalıştırarak ölçme) → **5) Karar kartı** |
| **Proje ve Değerlendirme** | ZIP projesinin statik taraması, İnsan vs YZ istatistiksel karşılaştırması, kural bazında precision/recall |
| **Katmanlar ve Sunum Rehberi** | statik / dinamik / LLM katmanlarının anlatımı, AST ağacı, elle dinamik ölçüm, sunumda ne söylenmeli |

**Hibrit zincir:** AST bulur → LLM doğrular ya da eler → çalıştırma ölçer. LLM'in kararı ve açıklaması yalnızca gerçek (kural, satır) çiftleri için, eleme kararı yalnızca gerekçesiyle kabul edilir; elenen bulgu silinmez (gri ve gerekçeli kalır, skoru ayrıca gösterilir). AST'nin kaçırdığı sorunların satırı ve alıntısı gerçek koda uymalıdır. Düzeltme ancak statik doğrulamadan geçerse, test girdileri ancak geçerli JSON ise kabul edilir. Davranış ve hız LLM'e
sorulmaz, Docker'da (ya da uyarılı yerel yedek modda) ölçülür. Ölçüm yalnızca denenen girdiler için geçerlidir.

**Sıfır tolerans ve tam davranış karşılaştırması:** düşük önemli bile olsa yeni bulgu doğuran düzeltme reddedilir. Dinamik analizde değişen **her** genel fonksiyon kendi test girdisiyle sınanır (girdisiz kalan fonksiyon varsa karar "kısmen" olur); dönüş değeri, `print` çıktısı, hata ve girdilerin yerinde değişmesi birebir karşılaştırılır. Statik kural bir performans bulgusunu çözülmüş saydığı halde ölçümde hız kazancı yoksa uyarı verilir.

## Testler

```bash
python -m unittest discover -s tests -t .      # ya da: pip install -r requirements-dev.txt && pytest
```

Testler `unittest` ile yazıldığı için ek paket gerektirmez; pytest de aynen toplar. Testler arasında projenin
**kendi kodunu** kendi analizinden geçirme (`tests/test_self_and_safety.py`) ve ilk code review'daki her hata için regresyon
testi vardır.

## Klasörler

| Yol | İçerik |
|---|---|
| `quality_studio/` | motor, kurallar, metrikler, yama, rapor, istatistik, benchmark, LLM katmanı, hibrit birleştirme, CLI |
| `app.py` | tek Streamlit arayüzü |
| `tests/` | birim ve regresyon testleri |
| `data/example_dataset.csv` | veri seti **format örneği** (bilimsel kanıt değil, bkz. teknik rapor §6) |
| `examples/sample_target.py` | deneme dosyası |
| `docs/TEKNIK_RAPOR.md` | mimari, yöntem, ölçümler, sınırlamalar, review maddelerinin durumu |

## Dürüst notlar

- Gerçek Gemini çağrısı ve hibrit akışın tamamı (LLM + Docker) geliştirme ortamında uçtan uca **denenmedi**; sahte taşıma katmanı,
  sahte `streamlit` ve yerel ölçüm ile test edildi. Hibrit sekmesini kendi bilgisayarınızda bir kez elle deneyin.
- Docker benchmark, ruff/bandit adaptörleri ve Streamlit arayüzü geliştirme ortamında **gerçek bileşenlerle çalıştırılmadı**
  (ağ yok, kurulu değildi); sahte süreçler/sahte `streamlit` modülü ile test edildi. İlk kurulumda elle deneyin.
- Kural değerlendirmesi yazar etiketli, küçük ve sentetik bir kümedir (139 parça); gerçek dünya doğruluğu değildir.
- Gerçek bir "insan vs. YZ" veri seti henüz yok.

Ayrıntılar için `docs/TEKNIK_RAPOR.md`, değişiklikler için `CHANGELOG.md`.
