# ORNEK 5: Yavas calisan kod
# Beklenen: PERF101 (3 seviyeli ic ice dongu), PERF102 (dongude count),
#           PERF103 (listeyi + ile buyutme)

def ortak_elemanlar(a, b):
    sonuc = []
    for x in a:
        for y in b:
            if x == y:
                sonuc.append(x)
    return sonuc


def uclu_toplam(a, b, c):
    bulunan = []
    for x in a:
        for y in b:
            for z in c:
                if x + y == z:
                    bulunan.append((x, y, z))
    return bulunan


def tekrar_sayilari(liste):
    sayilar = {}
    for oge in liste:
        sayilar[oge] = liste.count(oge)
    return sayilar


def kareler(n):
    sonuc = []
    for i in range(n):
        sonuc = sonuc + [i * i]
    return sonuc
