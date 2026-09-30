"""
Обработка PDF-чеков, присланных пользователем боту.

Шаги обработки (по порядку):
  1. Скачать файл
  2. Достать текст из PDF и распознать поля чека
  3. Проверить статус перевода ("Успешно")
  4. Проверить номер счёта получателя
  5. Проверить, что такой квитанции ещё не было (дубль)
  6. Разобрать комментарий на список людей
  7. Проверить, что сумма перевода не меньше ожидаемой (цена * число людей)
  8. Записать оплату в базу (статус 'pending')
  9. Сгенерировать коды, сохранить гостей, пометить оплату как 'sent'
  10. Отправить пользователю коды входа
"""

import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from config import RECEIVER_CONTRACT
from database import (
    add_payment,
    mark_payment_sent,
    add_guest,
    get_current_price,
    receipt_exists,
)
from code_generator import generate_code
from receipt_parser import (
    extract_text_from_pdf,
    parse_receipt_text,
    parse_comment,
    ReceiptParseError,
)
from logger_setup import logger


# Тексты ошибок — собраны в одном месте, чтобы легко было поменять формулировку
ERROR_NOT_PDF = "Пожалуйста, пришлите именно файл чека (PDF), а не скриншот или фото."
ERROR_UNKNOWN = "Не удалось обработать чек. Обратитесь к организаторам."
ERROR_STATUS = "В чеке указан неуспешный статус перевода. Проверьте, прошла ли оплата."
ERROR_RECEIVER = "Номер счёта получателя в чеке не совпадает с нашим. Проверьте, на тот ли счёт вы перевели деньги."
ERROR_DUPLICATE = "Этот чек уже был обработан ранее."
ERROR_FORMAT = "Проблема с форматом комментария, обратитесь к организаторам."
ERROR_AMOUNT = "Проблема с суммой оплаты, обратитесь к организаторам."


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вызывается, когда пользователь присылает боту файл (документ)."""
    user = update.effective_user
    document = update.message.document

    if document is None:
        return

    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text(ERROR_NOT_PDF)
        return

    logger.info(f"Получен чек от user_id={user.id} (@{user.username}): {document.file_name}")

    # Скачиваем файл во временную папку, которая автоматически удалится
    # после выхода из блока "with" — чек нам нужен только на пару секунд для разбора.
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = os.path.join(tmp_dir, document.file_name)
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(file_path)

        try:
            text = extract_text_from_pdf(file_path)
            parsed = parse_receipt_text(text)
        except ReceiptParseError as e:
            logger.warning(f"Не удалось распознать чек от {user.id}: {e}")
            await update.message.reply_text(ERROR_UNKNOWN)
            return
        except Exception as e:
            # Сюда попадём, если файл вообще не открылся как PDF, повреждён и т.п.
            logger.error(f"Ошибка при чтении PDF от {user.id}: {e}")
            await update.message.reply_text(ERROR_UNKNOWN)
            return

    # --- Проверка статуса перевода ---
    if parsed.status.lower() != "успешно":
        await update.message.reply_text(ERROR_STATUS)
        return

    # --- Проверка счёта получателя (если задан в .env) ---
    if RECEIVER_CONTRACT and parsed.receiver_contract != RECEIVER_CONTRACT:
        await update.message.reply_text(ERROR_RECEIVER)
        return

    # --- Быстрая проверка на дубль (финальная и надёжная — чуть ниже, в add_payment) ---
    if receipt_exists(parsed.receipt_number):
        await update.message.reply_text(ERROR_DUPLICATE)
        return

    # --- Разбор комментария на список людей ---
    try:
        parsed_comment = parse_comment(parsed.comment_raw)
    except ReceiptParseError as e:
        logger.warning(f"Не распознан комментарий от {user.id}: {e}")
        await update.message.reply_text(ERROR_FORMAT)
        return

    # --- Проверка суммы ---
    price = get_current_price()
    expected_amount = price * len(parsed_comment.people)
    if parsed.amount < expected_amount:
        logger.warning(
            f"Недостаточная сумма от {user.id}: пришло {parsed.amount}, "
            f"ожидалось {expected_amount} (цена {price} x {len(parsed_comment.people)} чел.)"
        )
        await update.message.reply_text(ERROR_AMOUNT)
        return

    # --- Записываем оплату в базу со статусом 'pending' ---
    payment_id = add_payment(
        receipt_number=parsed.receipt_number,
        amount=parsed.amount,
        sender_user_id=user.id,
        sender_tag=user.username,
        comment_raw=parsed.comment_raw,
        price_used=price,
    )

    if payment_id is None:
        # Сработала защита от дубля на уровне базы данных
        # (например, два одинаковых чека прислали одновременно)
        await update.message.reply_text(ERROR_DUPLICATE)
        return

    # --- Генерируем коды и сохраняем гостей ---
    try:
        codes_lines = []
        for person in parsed_comment.people:
            code = generate_code()

            # Если в комментарии были указаны бесплатные дети (формат "@tag N*"),
            # дописываем их количество прямо к тегу/телефону родителя — отдельный
            # код детям не нужен, они проходят по коду родителя.
            display_name = person
            if parsed_comment.children_count > 0:
                display_name = f"{person} {parsed_comment.children_count}дет"

            add_guest(code=code, tag_or_phone=display_name, payment_id=payment_id)
            codes_lines.append(f"{code} - {display_name}")

        # Помечаем оплату как полностью обработанную только ПОСЛЕ того,
        # как все коды точно сохранены в базе. Если бот упадёт раньше этой строки,
        # оплата останется в статусе 'pending' и её увидят при следующем запуске бота.
        mark_payment_sent(payment_id)
    except Exception as e:
        logger.error(f"Ошибка при выдаче кодов для payment_id={payment_id}: {e}")
        await update.message.reply_text(
            "Оплата принята, но при выдаче кодов произошла ошибка. "
            "Обратитесь к организаторам — код будет выдан вручную."
        )
        return

    reply_text = (
        "Вижу, ты уже оплатил билет! Лови номерки для входа, их нужно назвать на входе:\n\n"
        + "\n".join(codes_lines)
    )
    await update.message.reply_text(reply_text)
    logger.info(f"Коды выданы для payment_id={payment_id}: {codes_lines}")
