# Teknik Rapor — Code Quality Studio 2.0

> Bu rapor aracın ne yaptığını, nasıl ölçüldüğünü ve **nerede yetersiz kaldığını** anlatır. Tablolardaki
> sayılar `python -m unittest discover -s tests -t .` ve arayüzdeki "Rule Evaluation" sekmesiyle
> yeniden üretilebilir.

## 1. Amaç ve kapsam

Python kaynak kodunu **çalıştırmadan** (yalnızca `ast` ile ayrıştırarak) analiz eden, bulguları güvenlik,
performans, code smell, stil, güvenilirlik, tekrar eden kod ve "yapay zekâ üretimi göstergesi" kategorilerinde
raporlayan bir araç. Ek olarak: muhafazakâr otomatik yama önerileri, önce/sonra etki analizi, ZIP/dizin
taraması, etiketli veri setlerinde istatistiksel karşılaştırma ve kural bazında precision/recall ölçümü.

Kapsam dışı (bilinçli): LLM katmanı (şimdilik API anahtarı kullanılmıyor), veri akışı/taint analizi,
Python dışı diller, çalışma zamanı doğrulaması (yalnızca isteğe bağlı Docker benchmark).

## 2. Mimari

```
quality_studio/
  engine.py        analyze(): parse -> kural modülleri -> noqa -> (ruff/bandit) -> metrik+skor
  rules/           catalog.py (36 kural kaydı) + security/smell/performance/redundancy/ai_patterns
  metrics.py       tek karmaşıklık tanımı, yapı metrikleri, skor formülü
  refactor.py      yama önerisi/uygulama (bayt->karakter ofset dönüşümü ile)
  impact.py        önce/sonra karşılaştırma (statik)
  compare.py       istatistiksel veri seti karşılaştırması
  evaluation.py    ground_truth.py üzerinde precision/recall + Wilson aralıkları
  external.py      isteğe bağlı ruff/bandit çapraz doğrulama
  project_scan.py  ZIP/dizin tarama (zip-slip, boyut, şifre kontrolleri)
  report.py        text / JSON / SARIF 2.1.0 / HTML
  benchmark.py     isteğe bağlı Docker izole benchmark
  cli.py           çıkış kodlu komut satırı
app.py             tek Streamlit arayüzü (4 sekme)
```

Her kural `Context.report()` üzerinden, katalogdaki varsayılan önem/güven/CWE ile bulgu üretir.
Eşikler `Config` içindedir (`--config dosya.json` ile değiştirilebilir); kurallar `--disable` ile kapatılır,
satır bazında `# noqa: KURAL` veya `# cqs: ignore[KURAL]` ile bastırılır.

## 3. Kural kataloğu

| ID | Kural | Kategori | Önem | Güven | CWE |
|---|---|---|---|---|---|
| SEC101 | Use of eval()/exec() | Security | high | high | CWE-95 |
| SEC102 | Unsafe deserialization | Security | high | high | CWE-502 |
| SEC103 | Subprocess with shell=True | Security | high | high | CWE-78 |
| SEC104 | Dynamically built SQL query | Security | high | medium | CWE-89 |
| SEC105 | Possible hardcoded credential | Security | high | medium | CWE-798 |
| SEC106 | Shell command execution | Security | high | medium | CWE-78 |
| SEC107 | Unsafe YAML loading | Security | high | high | CWE-502 |
| SEC108 | Weak hash function | Security | medium | medium | CWE-327 |
| SEC109 | TLS verification disabled | Security | high | high | CWE-295 |
| SEC110 | assert used in runtime code | Security | low | medium | CWE-703 |
| SEC111 | Insecure temporary file name | Security | medium | high | CWE-377 |
| CQ101 | Long function | Code Smell | medium | high | - |
| CQ102 | Too many parameters | Code Smell | low | high | - |
| CQ107 | High cyclomatic complexity | Code Smell | medium | high | - |
| CQ108 | Deeply nested control flow | Code Smell | low | high | - |
| CQ103 | Public function missing docstring | Style | low | high | - |
| CQ104 | Comparison to None with ==/!= | Style | low | high | - |
| CQ106 | Comparison to True/False with ==/!= | Style | low | high | - |
| CQ105 | Mutable default argument | Reliability | medium | high | CWE-665 |
| CQ201 | Bare except clause | Reliability | medium | high | CWE-396 |
| CQ202 | Silenced exception | Reliability | medium | high | CWE-390 |
| PERF101 | Nested loops | Performance | low | low | - |
| PERF102 | count() called inside a loop | Performance | low | low | - |
| PERF103 | List rebuilt by concatenation inside a loop | Performance | medium | medium | - |
| RED001 | Unused import | Redundancy | low | high | - |
| RED002 | Unused local variable | Redundancy | low | high | - |
| RED003 | Duplicated code block | Redundancy | low | medium | - |
| RED004 | Duplicated function body | Redundancy | medium | medium | - |
| RED005 | Unreachable code | Redundancy | low | high | - |
| RED006 | Redundant pass statement | Redundancy | low | high | - |
| AIP001 | Temporary variable returned immediately | AI Pattern | info | low | - |
| AIP002 | Broad exception swallowed with a default | AI Pattern | info | low | - |
| AIP003 | Comments that restate the code | AI Pattern | info | low | - |
| AIP004 | Boilerplate docstring | AI Pattern | info | low | - |
| AIP005 | Overly verbose identifier | AI Pattern | info | low | - |
| SYN001 | Syntax error | Syntax | high | high | - |

Güven (`confidence`) bulgunun kesinliğidir: örneğin `SEC105` bir isim/değer örüntüsüdür (medium), `SEC101`
doğrudan `eval` çağrısıdır (high). İç içe döngü (`PERF101`) ve `count()` (`PERF102`) kuralları veri boyutunu
bilmediği için düşük güvenlidir ve ipucu olarak okunmalıdır.

## 4. Yöntem

**Karmaşıklık.** Tek tanım: fonksiyon kapsamındaki karar noktaları (`if`, üçlü ifade, `for`, `while`, `except`,
comprehension ve içindeki `if`, `and/or`, `match case`) + 1. `and/or` sayıldığı için klasik McCabe'den biraz
sıkıdır; bu yüzden `CQ107` eşiği 15'tir.

**Skor.** `skor = 100 / (1 + yoğunluk/25)`, `yoğunluk = ağırlıklı_ceza × 100 / max(boş_olmayan_satır, 30)`;
ağırlıklar info 0, low 1, medium 2, high 5, critical 10. Skor ceza arttıkça monoton azalır (0'da doygunlaşmaz) ve
dosya boyutuna duyarlıdır. AI göstergeleri skora girmez. Skor bir **sezgiseldir**; doğruluk veya güvenlik kanıtı değildir.

**AI-üretimi göstergeleri (AIP001–AIP005).** Hemen dönen geçici değişken, aşırı savunmacı `try/except` (geniş
yakalayıp varsayılan döndürme/print), kodu tekrar eden yorumlar (≥3), şablon docstring, aşırı uzun isimler.
Bunlar **zayıf, olasılıksal sinyallerdir**; yazarlığı kanıtlamaz, önemleri `info`'dur. Bu göstergelerin gerçek insan/YZ
kodunu ayırt etme başarısı **ölçülmedi** (bkz. §6 ve §10).

**Çapraz doğrulama (hibrit statik hat).** `--external` ile ve kuruluysa `ruff` ile `bandit` metni okuyarak çalıştırılır;
aynı satırdaki eşdeğer bulgular AST bulgusuna `corroborated_by` olarak eklenir, eşleşmeyenler ayrı bulgu olarak listelenir.
Bu bağdaştırıcılar birim testlerde sahte çıktıyla sınandı; **gerçek ruff/bandit ikilileriyle çalıştırılmadı**
(geliştirme ortamında kurulu değildi).

**Yama güvenliği.** `ast` sütun ofsetleri UTF-8 bayttır; `refactor.py` bunları karakter indeksine çevirir. Yamalar
çakışma ve bayat-kaynak denetiminden geçer, sonuç yeniden ayrıştırılır. Davranışın korunduğu **iddia edilmez**; her yama
bir uyarı metni taşır.

## 5. Kural değerlendirmesi (ölçülebilir)

`quality_studio/ground_truth.py` içinde **139 elle etiketlenmiş kod parçası** var. Her parçada tetiklenmesi gereken
kuralların **tam kümesi** kodu okuyarak belirlendi; fazladan tetiklenen her kural yanlış pozitif sayılır. Üç bilinen
sınırlama bilerek pozitif olarak bırakıldı (`run = eval; run(x)`, `getattr(os, "system")`, anahtarı değişkende tutan
sözlük) ki recall şişmesin.

| Ölçüt | Değer |
|---|---|
| Vaka sayısı | 139 |
| Pozitif etiket | 73 |
| TP / FP / FN | 70 / 0 / 3 |
| Precision | 1.0 (%95 Wilson: 0.948–1.0) |
| Recall | 0.959 (%95 Wilson: 0.886–0.986) |
| F1 | 0.979 |

| Kural | Destek | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|---|
| AIP001 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| AIP002 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| AIP003 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| AIP004 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| AIP005 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| CQ101 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| CQ102 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| CQ103 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| CQ104 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| CQ105 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| CQ106 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| CQ107 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| CQ108 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| CQ201 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| CQ202 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| PERF101 | 4 | 4 | 0 | 0 | 1.0 | 1.0 |
| PERF102 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| PERF103 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| RED001 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| RED002 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| RED003 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| RED004 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| RED005 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| RED006 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| SEC101 | 3 | 2 | 0 | 1 | 1.0 | 0.667 |
| SEC102 | 3 | 3 | 0 | 0 | 1.0 | 1.0 |
| SEC103 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| SEC104 | 5 | 5 | 0 | 0 | 1.0 | 1.0 |
| SEC105 | 8 | 7 | 0 | 1 | 1.0 | 0.875 |
| SEC106 | 4 | 3 | 0 | 1 | 1.0 | 0.75 |
| SEC107 | 3 | 3 | 0 | 0 | 1.0 | 1.0 |
| SEC108 | 3 | 3 | 0 | 0 | 1.0 | 1.0 |
| SEC109 | 2 | 2 | 0 | 0 | 1.0 | 1.0 |
| SEC110 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| SEC111 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |
| SYN001 | 1 | 1 | 0 | 0 | 1.0 | 1.0 |

**Bu sayılar nasıl okunmalı.**

1. Etiketleri aracın yazarı yazdı ve araç aynı kişi tarafından geliştirildi; bu bir **regresyon/akıl sağlığı testidir**,
   gerçek dünya kodunda doğruluk tahmini değildir. Precision'ın 1.0 çıkması bu yüzden şaşırtıcı değildir.
2. Birçok kuralın desteği 1–3 vakadır; kural bazındaki 1.0 değerleri istatistiksel olarak zayıftır. Genel aralıklar
   (Wilson) toplam örnek sayısına göre hesaplanmıştır.
3. İlk çalıştırmada iki uyuşmazlık çıktı ve ikisinde de **etiket hatalıydı** (motor haklıydı): 52 satırlık bir test
   fonksiyonu gerçekten `CQ101` tetikliyordu; bir parçadaki `x = ...; return x` gerçekten `AIP001`'di. Parçalar düzeltildi.
   Kural tasarımı değiştiğinde (iç içe döngü) etiketler önce anlamsal olarak yeniden düşünüldü, sonra güncellendi.
4. Kalan 3 FN yukarıdaki bilinçli sınırlamalardır (AST ile görülemez; veri akışı analizi gerekir).

## 6. İstatistiksel karşılaştırma

Etiketli veri setinde (`label,code`) her örnek için **kusur yoğunluğu** (100 boş olmayan satırdaki bulgu; AI göstergeleri hariç)
ve **AI-gösterge yoğunluğu** ayrı ayrı hesaplanır. Gruplar çiftler halinde karşılaştırılır:

- **Mann-Whitney U** (iki yönlü; yoğunluklar normal dağılmadığı için t-testi yerine),
- **Cliff's delta** etki büyüklüğü (|d| < .147 önemsiz, < .33 küçük, < .474 orta, aksi halde büyük),
- ortalama farkı için **bootstrap %95 güven aralığı** (2000 yeniden örnekleme, sabit tohum),
- ≥3 etiket varsa **Bonferroni** düzeltmeli p,
- sözdizimi hatalı örneklerin oranı için **Fisher exact** testi.

**Yanlılık kontrolü.** Ayrıştırılamayan örnekler yoğunluk istatistiğinden çıkarılır ama etiket başına **sayılır ve
raporlanır**; çünkü bir grup daha sık bozuk kod üretiyorsa sessizce atmak sonucu çarpıtır. Bir gruptaki geçerli örnek sayısı
10'dan azsa uyarı verilir.

**Demo veri seti.** `data/example_dataset.csv` (10 "human", 10 "ai") yalnızca **format örneğidir**. Örnekleri tek bir kişi
yazdı ve "ai" örnekleri AIP göstergelerini taşıyacak biçimde kuruldu; bu yüzden göstergelerin "ayırt etmesi" döngüseldir ve
hiçbir bilimsel iddiaya temel olamaz. Gerçek bir karşılaştırma için kaynağı belgelenmiş (ör. aynı görevler için insan çözümleri
ve model çıktıları) bir veri seti gerekir; **böyle bir veri seti bu projede henüz yoktur**.

## 7. Aracı kendi kodunda çalıştırma

İlk sürümde noktalı virgüllü, tek satırda çok ifadeli kod vardı. Yeni kod tabanı kendi analizinden **0 bulgu** ile geçer
(`python -m quality_studio quality_studio app.py --fail-on low`) ve bu bir testtir (`tests/test_self_and_safety.py`).
Süreçte ilk tarama 88 bulgu verdi (kural revizyonlarından önceki sürümle); bunların bir kısmı aracın kendi hatalarını açığa çıkardı:

- `elif` zinciri derin iç içelik sayılıyordu → düzeltildi (`CQ108`);
- derinlik-2 iç içe döngüler her yerde gürültü üretiyordu (matris/ızgara dolaşımı) → bir döngünün üzerinde döndüğü ifade dış
  döngü değişkenine bağlıysa (`for cell in row`) çarpan sayılmıyor; `PERF101` artık derinlik ≥3 veya aynı koleksiyon üzerinde
  tüm-çiftler (`for a in xs: for b in xs`) için tetikleniyor.

## 8. İlk code review maddelerinin durumu

| Madde | Durum |
|---|---|
| Sızan API anahtarı | `.env` ve tüm LLM kodu silindi. **Anahtarın iptal edilmesi kullanıcının işi** (eski zip'te duruyor). |
| AI-generation pattern tespiti | Yapıldı (AIP001–005), ama yalnızca zayıf göstergeler; doğrulama yok (§6). |
| Redundant code | Yapıldı (RED001–006). |
| Hibrit motor (AST + LLM) | LLM katmanı **kasıtlı olarak yok**. AST + isteğe bağlı ruff/bandit çapraz doğrulama var (gerçek ikililerle denenmedi). |
| Statik analiz hattı | 36 kural + ruff/bandit adaptörleri. |
| Kategori ayrımı | Security / Performance / Code Smell / Style / Reliability / Redundancy / AI Pattern / Syntax. |
| İstatistiksel karşılaştırma | Yapıldı (§6); **gerçek veri seti yok**. |
| Ölçülebilir değerlendirme | Yapıldı (§5); etiketler yazar tarafından, küçük ve sentetik. |
| Teknik rapor | Bu belge. |
| ZIP proje taraması | Arayüze ve CLI'ya bağlandı. |
| Unicode yama hatası | Düzeltildi + testler (Türkçe karakter, CRLF, form feed). |
| Benchmark süre şişmesi | Düzeltildi; yerelde tracemalloc ~16× yavaşlatma ölçüldü, yeni runner zamanı ayrı turda ölçer. Docker içinde çalıştırılmadı. |
| Docker zaman aşımı / artık container | İmaj çekme ayrı timeout; `--name` + `docker kill/rm`. Sahte süreçlerle test edildi; gerçek Docker'la denenmedi. |
| SEC104 / PERF102 / SEC105 hataları | Düzeltildi + regresyon testleri. |
| Eksik güvenlik kuralları | SEC107–SEC111, CQ105, CQ106 eklendi. |
| İç içe döngü sayımı | for/while/comprehension; gürültü azaltıldı (§7). |
| CQ101 satır sayısı | `end_lineno - lineno + 1`. |
| Skor doygunluğu | Boyuta duyarlı, monoton skor (§4). |
| Üç karmaşıklık formülü | Tek tanım (`metrics.py`). |
| Dataset sessiz yanlılık / KeyError / csv limiti | Sayılıp raporlanıyor; sütun kontrolü; limit kaldırıldı. |
| Kendi kodunda çalıştırma | Yapıldı (§7). |
| Ölü/bozuk dosyalar, dağınık sürümleme | Silindi; tek README + CHANGELOG. |
| Kural altyapısı, SARIF/JSON, çıkış kodu, `# noqa` | Yapıldı. |
| Model adlarının doğrulanması | LLM kodu kaldırıldığı için konu dışı kaldı. |

## 9. Tehdit modeli

Analiz edilen kod **hiçbir zaman içe aktarılmaz veya çalıştırılmaz**. ZIP'te zip-slip (`..`, mutlak yol), şifreli üye, boyut ve
dosya sayısı sınırları denetlenir. HTML çıktıları kaçışlanır. Tek istisna isteğe bağlı Docker benchmark'tır: ağ yok, salt-okunur
dosya sistemi, tüm yetenekler düşürülmüş, root dışı kullanıcı, CPU/bellek/PID sınırı, ayrı zaman aşımı. Docker mükemmel bir
güvenlik sınırı değildir; çok kiracılı veya halka açık kullanımda düşmanca kod için microVM gibi daha güçlü yalıtım gerekir.

## 10. Bilinen sınırlamalar ve sonraki adımlar

- Veri akışı/taint analizi yok: yardımcı fonksiyonla kurulan SQL ve değişken üzerinden aktarılan tehlikeli çağrılar çoğunlukla kaçar.
- Kural değerlendirmesi yazar etiketli ve küçük; bağımsız etiketleyici ve gerçek depo örnekleri gerekir.
- AI göstergeleri doğrulanmadı; gerçek insan/YZ veri setiyle ROC/AUC ölçülmeden tespit iddiası yapılmamalıdır.
- ruff/bandit adaptörleri gerçek ikililerle, Docker akışı gerçek Docker'la, arayüz gerçek Streamlit'le elle denenmeli
  (geliştirme ortamında bunlar kurulu değildi; arayüz sahte bir `streamlit` modülüyle duman testinden geçirildi).
- LLM katmanı eklenirse yalnızca açıklama/aday üretimi için, çıktısı AST ile çapraz doğrulanarak kullanılmalıdır.
