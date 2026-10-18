
import os
import json
import pickle
import hashlib
import subprocess
import time

DATABASE = []
ADMIN_PASSWORD = "admin123"
API_KEY = "sk-test-123456789"


class UserManager:

    def __init__(self):
        self.users = []
        self.logs = []
        self.cache = {}

    def add_user(self, name, email, age):
        if name == None:
            return False

        if email == None:
            return False

        if age == None:
            return False

        user = {
            "name": name,
            "email": email,
            "age": age,
            "active": True
        }

        self.users.append(user)
        return True

    def find_user(self, email):
        for user in self.users:
            if user["email"] == email:
                return user
        return None

    def get_active_users(self):
        result = []

        for user in self.users:
            if user["active"] == True:
                result.append(user)

        return result

    def find_duplicate_emails(self):
        duplicates = []

        for user in self.users:
            count = 0

            for other in self.users:
                if user["email"] == other["email"]:
                    count += 1

            if count > 1:
                duplicates.append(user["email"])

        return duplicates

    def calculate_average_age(self):
        total = 0

        for user in self.users:
            total += user["age"]

        if len(self.users) == 0:
            return 0

        return total / len(self.users)

    def generate_report(self):
        report = ""

        for user in self.users:
            report = report + user["name"]
            report = report + " - "
            report = report + user["email"]
            report = report + "\n"

        return report

    def authenticate(self, email, password):
        if password == "admin123":
            return True

        for user in self.users:
            if user["email"] == email:
                if password == user.get("password"):
                    return True

        return False

    def save_users(self, filename):
        try:
            file = open(filename, "w")
            json.dump(self.users, file)
            file.close()
        except:
            print("Error")

    def load_users(self, filename):
        try:
            file = open(filename, "rb")
            self.users = pickle.load(file)
            file.close()
        except:
            return []

    def delete_user(self, email):
        for user in self.users:
            if user["email"] == email:
                self.users.remove(user)
                return True

        return False

    def search_users(self, keyword):
        results = []

        for user in self.users:
            if keyword in user["name"]:
                results.append(user)

        return results

    def sort_users_by_age(self):
        for i in range(len(self.users)):
            for j in range(len(self.users) - 1):
                if self.users[j]["age"] > self.users[j + 1]["age"]:
                    temp = self.users[j]
                    self.users[j] = self.users[j + 1]
                    self.users[j + 1] = temp

        return self.users

    def execute_backup(self, filename):
        command = "cp users.json " + filename
        os.system(command)

    def run_external_command(self, command):
        subprocess.run(command, shell=True)

    def calculate_password_hash(self, password):
        return hashlib.md5(password.encode()).hexdigest()

    def log_activity(self, message):
        self.logs.append(message)
        print(message)

    def process_numbers(self, numbers):
        result = []

        for number in numbers:
            if number % 2 == 0:
                result.append(number * 2)

        return result

    def expensive_calculation(self, numbers):
        result = []

        for number in numbers:
            for other in numbers:
                result.append(number * other)

        return result

    def count_occurrences(self, numbers):
        result = {}

        for number in numbers:
            result[number] = numbers.count(number)

        return result

    def check_access(self, user):
        if user == None:
            return False

        if user["active"] == True:
            if user["age"] > 18:
                if user["email"] != None:
                    return True

        return False


def main():
    manager = UserManager()

    manager.add_user("Alice", "alice@example.com", 25)
    manager.add_user("Bob", "bob@example.com", 30)
    manager.add_user("Charlie", "charlie@example.com", 22)
    manager.add_user("Alice2", "alice@example.com", 28)

    print(manager.get_active_users())
    print(manager.find_duplicate_emails())
    print(manager.calculate_average_age())
    print(manager.generate_report())
    print(manager.sort_users_by_age())

    numbers = [1, 2, 3, 4, 5, 2, 3, 2]

    print(manager.process_numbers(numbers))
    print(manager.expensive_calculation(numbers))
    print(manager.count_occurrences(numbers))


if __name__ == "__main__":
    main()
