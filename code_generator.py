"""
Генерация уникальных кодов входа.

Формат кода: 3 заглавные латинские буквы + 3 цифры, например ABC123.
Всего возможных комбинаций: 26^3 * 10^3 = 17 576 000 — гостей у нас
максимум несколько сотен, так что случайной коллизии почти не бывает,
но на всякий случай функция сама проверяет уникальность по базе данных.
"""

import random
import string

from database import code_exists

LETTERS = string.ascii_uppercase
DIGITS = string.digits


def generate_code() -> str:
    """
    Генерирует один код и проверяет, что такого ещё нет в базе.
    Если код уже занят (крайне редкий случай) — просто пробует ещё раз.
    """
    while True:
        letters_part = "".join(random.choices(LETTERS, k=3))
        digits_part = "".join(random.choices(DIGITS, k=3))
        code = letters_part + digits_part

        if not code_exists(code):
            return code
