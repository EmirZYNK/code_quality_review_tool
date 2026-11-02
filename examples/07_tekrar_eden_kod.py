# ORNEK 7: Kopyala-yapistir kod
# Beklenen: RED004 (ayni govdeli iki fonksiyon), RED003 (tekrar eden blok)

def dikdortgen_alan(en, boy):
    sonuc = en * boy
    sonuc = round(sonuc, 2)
    print("Alan hesaplandi")
    return sonuc


def kare_alan(en, boy):
    sonuc = en * boy
    sonuc = round(sonuc, 2)
    print("Alan hesaplandi")
    return sonuc


def rapor(a, b):
    print("--- Rapor basladi ---")
    toplam = a + b
    fark = a - b
    carpim = a * b
    print(toplam, fark, carpim)
    print("--- Rapor bitti ---")
    print("--- Rapor basladi ---")
    toplam = a + b
    fark = a - b
    carpim = a * b
    print(toplam, fark, carpim)
    print("--- Rapor bitti ---")
