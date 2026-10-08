"""
Разбор PDF-чека Т-Банка.

Личность плательщика теперь определяется напрямую из Telegram-чата
(username или номер телефона, который он сам пришлёт боту), поэтому текст
поля "Сообщение" из чека нам больше не нужен — читаем только служебные поля:
номер квитанции, сумму, статус и номер счёта получателя.
"""

import re
from dataclasses import dataclass


class ReceiptParseError(Exception):
    """Не удалось распознать чек (не тот формат / повреждённый файл)."""
    pass


@dataclass
class ParsedReceipt:
    receipt_number: str
    amount: int
    status: str
    receiver_contract: str


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
    # Сумма может быть записана как "1500 ₽", "1 500 ₽" (пробел тысяч) или "1500,50 ₽"
    amount_str = find(r"Сумма\s+(\d[\d\s\u00a0\u202f]*(?:[.,]\d{1,2})?)\s*₽", "сумма")
    amount_str = re.sub(r"[\s\u00a0\u202f]", "", amount_str).replace(",", ".")
    status = find(r"Статус\s+(\S+)", "статус")
    receiver_contract = find(r"Договор получателя\s+(\d+)", "договор получателя")

    return ParsedReceipt(
        receipt_number=receipt_number,
        amount=int(float(amount_str)),  # копейки отбрасываем: 999,50 -> 999 (меньше 1000, это верно)
        status=status,
        receiver_contract=receiver_contract,
    )
