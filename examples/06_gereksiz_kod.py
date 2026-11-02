# ORNEK 6: Gereksiz ve olu kod
# Beklenen: RED001 (kullanilmayan import), RED002 (kullanilmayan degisken),
#           RED005 (erisilemeyen kod), RED006 (gereksiz pass), CQ103

import os
import sys
import json


def topla(a, b):
    gecici = a * 100
    toplam = a + b
    return toplam
    print("Bu satir hic calismaz")


def selamla(ad):
    print("Merhaba", ad)
    pass
