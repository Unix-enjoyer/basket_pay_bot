"""
Разбор PDF-чека Т-Банка и разбор текста комментария (поле "Сообщение") из него.

Пример текста, который лежит внутри такого PDF (это НЕ картинка, а обычный
текст, поэтому его можно прочитать без распознавания изображений):

    Квитанция  № 1-111-442-569-826
    Статус  Успешно
    Сумма  1 ₽
    Договор получателя  8079209614
    Сообщение получателю; да; поехали; скорее

Если Т-Банк немного поменяет вёрстку чека — этот файл придётся обновить,
но вся логика "поиска по чеку" собрана здесь в одном месте, чтобы это было
легко сделать, не трогая остальной код бота.
"""

import re
from dataclasses import dataclass, field
from typing import List


class ReceiptParseError(Exception):
    """Не удалось распознать чек или комментарий (не тот формат / повреждённый файл)."""
    pass


@dataclass
class ParsedReceipt:
    receipt_number: str    # номер квитанции, например "1-111-442-569-826"
    amount: int             # сумма перевода в рублях
    status: str              # текст статуса из чека, например "Успешно"
    receiver_contract: str  # номер договора получателя
    comment_raw: str        # текст поля "Сообщение", ещё не разобранный на людей


def extract_text_from_pdf(file_path: str) -> str:
    """Достаёт весь текст со всех страниц PDF-файла (обычно страница одна)."""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def parse_receipt_text(text: str) -> ParsedReceipt:
    """Достаёт нужные поля из текста PDF-чека с помощью регулярных выражений."""

    def find(pattern: str, field_name: str) -> str:
        match = re.search(pattern, text)
        if not match:
            raise ReceiptParseError(f"Не найдено поле в чеке: {field_name}")
        return match.group(1).strip()

    receipt_number = find(r"Квитанция\s*№\s*([\d-]+)", "номер квитанции")
    amount_str = find(r"Сумма\s+(\d+)\s*₽", "сумма")
    status = find(r"Статус\s+(\S+)", "статус")
    receiver_contract = find(r"Договор получателя\s+(\d+)", "договор получателя")

    # Поле "Сообщение" часто переносится на следующую строку, поэтому берём
    # весь текст от "Сообщение получателю;" до начала следующего блока чека.
    comment_match = re.search(
        r"Сообщение\s*(.+?)(?:Служба поддержки|По вопросам|Квитанция|$)",
        text,
        re.DOTALL,
    )
    if not comment_match:
        raise ReceiptParseError("Не найдено поле в чеке: сообщение получателю")

    # Склеиваем текст в одну строку, убирая переносы строк и лишние пробелы
    comment_raw = " ".join(comment_match.group(1).split())

    return ParsedReceipt(
        receipt_number=receipt_number,
        amount=int(amount_str),
        status=status,
        receiver_contract=receiver_contract,
        comment_raw=comment_raw,
    )


# ==================== Разбор комментария на список людей ====================

@dataclass
class ParsedComment:
    """
    Результат разбора текста комментария.
    people          — список тегов (@tag) и/или телефонов (+7...), за которых нужно выдать коды
    children_count  — число бесплатных детей (сценарий "я + N детей до 16 лет")
    """
    people: List[str] = field(default_factory=list)
    children_count: int = 0


# @тег: от 5 до 32 символов после @ (ограничение самого Telegram на длину username)
TAG_RE = r"@[A-Za-z0-9_]{5,32}"
# телефон в формате +79123456789 (11 цифр после +7)
PHONE_RE = r"\+7\d{10}"
PERSON_RE = rf"(?:{TAG_RE}|{PHONE_RE})"


def parse_comment(comment: str) -> ParsedComment:
    """
    Разбирает комментарий одного из трёх форматов, описанных в приветственном сообщении бота:

      1) "@tag"                — платит один человек за себя
      2) "@tag; @tag2; @tag3"  — платит один человек за себя и за гостей
      3) "@tag 2*"             — платит один человек за себя и N бесплатных детей

    Бросает ReceiptParseError, если формат не подходит ни под один из вариантов.
    """
    comment = comment.strip()

    # --- Сценарий 3: "@tag N*" (сам + дети) ---
    children_match = re.fullmatch(rf"({PERSON_RE})\s+(\d+)\*", comment)
    if children_match:
        person = children_match.group(1)
        children_count = int(children_match.group(2))
        return ParsedComment(people=[person], children_count=children_count)

    # --- Сценарии 1 и 2: список людей через ";" ---
    parts = [p.strip() for p in comment.split(";") if p.strip()]
    if not parts:
        raise ReceiptParseError("Комментарий пустой")

    for part in parts:
        if not re.fullmatch(PERSON_RE, part):
            raise ReceiptParseError(f"Не распознан формат части комментария: '{part}'")

    return ParsedComment(people=parts, children_count=0)
