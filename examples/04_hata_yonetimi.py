# ORNEK 4: Kotu hata yonetimi ve tehlikeli varsayilan argument
# Beklenen: CQ201 (bos except), CQ202 (hata yutuluyor), CQ105 (mutable default)

def sayiya_cevir(metin):
    try:
        return int(metin)
    except:
        return 0


def dosya_oku(yol):
    try:
        with open(yol) as f:
            return f.read()
    except OSError:
        pass


def listeye_ekle(oge, liste=[]):
    liste.append(oge)
    return liste


print(listeye_ekle(1))
print(listeye_ekle(2))   # Beklenmedik: [1, 2] yazar!
