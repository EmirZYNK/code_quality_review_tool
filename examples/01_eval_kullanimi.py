# ORNEK 1: Tehlikeli eval / exec kullanimi
# Beklenen: SEC101 (eval/exec), CQ103 (docstring yok)

def hesapla(ifade):
    # Kullanici "2+3" yazarsa calisir ama "__import__('os')..." yazarsa tehlikeli olur
    return eval(ifade)


def kod_calistir(kod):
    exec(kod)


print(hesapla("2 + 3"))
