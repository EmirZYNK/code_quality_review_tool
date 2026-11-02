# ORNEK 9: Stil hatalari
# Beklenen: CQ104 (== None), CQ106 (== True), CQ103 (docstring yok)

def kontrol(deger):
    if deger == None:
        return "bos"
    return "dolu"


def aktif_mi(durum):
    if durum == True:
        return "aktif"
    if durum != False:
        return "belirsiz"
    return "pasif"


def ikiyle_carp(x):
    return x * 2
