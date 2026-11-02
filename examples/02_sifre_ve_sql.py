# ORNEK 2: Gomulu sifre, zayif hash, SQL enjeksiyonu
# Beklenen: SEC105 (sifre), SEC108 (md5), SEC104 (SQL), CQ103

import hashlib
import sqlite3

password = "admin123"
api_key = "sk-1234567890abcdef"


def sifre_ozeti(sifre):
    return hashlib.md5(sifre.encode()).hexdigest()


def kullanici_bul(kullanici_adi):
    baglanti = sqlite3.connect("uygulama.db")
    sorgu = "SELECT * FROM kullanicilar WHERE ad = '" + kullanici_adi + "'"
    return baglanti.execute(sorgu).fetchall()
