import re
import json
import os


def clean(lines):
    result = []
    for line in lines:
        for word in line.split():
            if re.match(r"^[a-zA-Z]+$", word):
                result = result + [word.lower()]
    return result


def most_common(words, n):
    """En sık geçen n kelimeyi döndürür."""
    counts = {}
    for word in words:
        counts[word] = words.count(word)
    ranked = sorted(counts, key=counts.get, reverse=True)
    return ranked[:n]


def has_many_spaces(text):
    if text.count(" ") > 10:
        return True
    else:
        return False


def has_duplicates(items):
    for a in items:
        for b in items:
            if a is not b and a == b:
                return True
    return False


def write_json(path, data):
    result = json.dumps(data)
    with open(path, "w") as f:
        f.write(result)
    return result
