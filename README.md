# Code Quality Studio 2.0

Python kaynak kodunu **çalıştırmadan** (yalnızca AST ile) analiz eden araç: güvenlik, performans, code smell, stil,
güvenilirlik, tekrar eden kod ve "yapay zekâ üretimi göstergeleri". Önce/sonra etki analizi, muhafazakâr yama önerileri,
ZIP/dizin taraması, istatistiksel veri seti karşılaştırması ve kural bazlı precision/recall ölçümü içerir.

> LLM katmanı **isteğe bağlıdır**: API anahtarı girilmezse araç yalnızca statik/dinamik katmanlarla çalışır.
> Anahtar koda ya da dosyaya yazılmaz, yalnızca oturumda bellekte tutulur.
> Skor ve AI göstergeleri sezgiseldir; doğruluk, güvenlik veya yazarlık kanıtı değildir.

## Kurulum ve çalıştırma

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Arayüz tamamen Türkçedir. Sekmeler:

| Sekme | Ne gösterir |
|---|---|
| **Kod İnceleme** | bulgular, satır görünümü, yamalar, önce/sonra etki analizi |
| **Statik Analiz** | AST ağacı, hangi kuralın hangi düğümde tetiklendiği, kategori dağılımı, 36 kurallık katalog |
| **Dinamik Analiz** | önce/sonra kodunu Docker'da izole çalıştırıp süre, bellek ve çıktıyı karşılaştırır |
| **LLM Açıklama** | bulguları bu koda özgü Türkçe açıklar, kaçırılan adayları önerir, düzeltme önerir; her çıktı AST ile doğrulanır |
| **İnsan vs YZ** | etiketli veri setinde istatistiksel karşılaştırma |
| **Proje (ZIP)** | ZIP olarak yüklenen projenin statik taraması |
| **Kural Değerlendirmesi** | kural bazında precision/recall ölçümü |

## Komut satırı

```bash
python -m quality_studio dosya.py                     # tek dosya
python -m quality_studio proje_klasoru --format sarif --output sonuc.sarif
python -m quality_studio proje.zip --fail-on high     # CI kapısı
python -m quality_studio kod.py --disable PERF101,CQ103 --config esikler.json
python -m quality_studio kod.py --external            # kuruluysa ruff + bandit ile çapraz doğrulama
```

Çıkış kodu: `0` = `--fail-on` (varsayılan `medium`) seviyesinde bulgu yok, `1` = bulgu var, `2` = girdi/kullanım hatası.
Bir satırdaki bulguyu bastırmak için `# noqa: SEC105` veya `# cqs: ignore[SEC105]`.

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
| `quality_studio/` | motor, kurallar, metrikler, yama, rapor, istatistik, benchmark, CLI |
| `app.py` | tek Streamlit arayüzü |
| `tests/` | birim ve regresyon testleri |
| `data/example_dataset.csv` | veri seti **format örneği** (bilimsel kanıt değil, bkz. teknik rapor §6) |
| `examples/sample_target.py` | deneme dosyası |
| `docs/TEKNIK_RAPOR.md` | mimari, yöntem, ölçümler, sınırlamalar, review maddelerinin durumu |

## Dürüst notlar

- Docker benchmark, ruff/bandit adaptörleri ve Streamlit arayüzü geliştirme ortamında **gerçek bileşenlerle çalıştırılmadı**
  (ağ yok, kurulu değildi); sahte süreçler/sahte `streamlit` modülü ile test edildi. İlk kurulumda elle deneyin.
- Kural değerlendirmesi yazar etiketli, küçük ve sentetik bir kümedir (139 parça); gerçek dünya doğruluğu değildir.
- Gerçek bir "insan vs. YZ" veri seti henüz yok.

Ayrıntılar için `docs/TEKNIK_RAPOR.md`, değişiklikler için `CHANGELOG.md`.
