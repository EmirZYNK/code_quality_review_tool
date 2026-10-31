import hashlib
import random
import sqlite3
import string


def generate_password(length=8):
    letters = string.ascii_letters + string.digits
    password = ""
    for _ in range(length):
        password = password + random.choice(letters)
    return password


def password_digest(password):
    return hashlib.md5(password.encode()).hexdigest()


def find_user(connection, username):
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM users WHERE name = '" + username + "'")
    return cursor.fetchone()


def log_in(connection, username, password):
    admin_password = "admin123"
    try:
        record = find_user(connection, username)
        if record != None:
            if record[2] == password_digest(password):
                return True
            else:
                return False
        return False
    except:
        pass


if __name__ == "__main__":
    db = sqlite3.connect("users.db")
    print(log_in(db, "ali", "1234"))
