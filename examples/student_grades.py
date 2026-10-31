"""Öğrenci not takip sistemi: CSV dosyasından notları okuyup ortalama hesaplar."""
import csv
import os
import sys


def read_grades(file_path):
    """CSV dosyasındaki (name, grade) satırlarını sözlük listesi olarak döndürür."""
    records = []
    with open(file_path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            records.append({"name": row["name"], "grade": float(row["grade"])})
    return records


def is_passing(grade):
    """Not 50 veya üzerindeyse True döndürür."""
    if grade >= 50:
        return True
    else:
        return False


def average(records):
    """Kayıtların not ortalamasını döndürür; liste boşsa 0 verir."""
    if len(records) == 0:
        return 0
    total = 0
    for record in records:
        total = total + record["grade"]
    return total / len(records)


def print_report(records):
    """Her öğrencinin durumunu ekrana yazar."""
    for record in records:
        status = "passed" if is_passing(record["grade"]) else "failed"
        print(f"{record['name']}: {record['grade']} ({status})")
    print(f"Class average: {average(records):.1f}")
    return None
    print("report finished")


if __name__ == "__main__":
    print_report(read_grades(sys.argv[1]))
