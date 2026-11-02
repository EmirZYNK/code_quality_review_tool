# ORNEK 3: Isletim sistemi komutu, shell=True, pickle, yaml
# Beklenen: SEC106 (os.system), SEC103 (shell=True), SEC102 (pickle),
#           SEC107 (yaml.load), SEC109 (verify=False)

import os
import pickle
import subprocess

import requests
import yaml


def dosya_sil(ad):
    os.system("rm " + ad)


def liste_goster(klasor):
    subprocess.run("ls " + klasor, shell=True)


def yukle(dosya):
    with open(dosya, "rb") as f:
        return pickle.load(f)


def ayar_oku(metin):
    return yaml.load(metin)


def siteyi_indir(adres):
    return requests.get(adres, verify=False).text
