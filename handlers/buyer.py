"""
Сценарий регистрации (оформления билета) обычным пользователем.

Шаги (каждый — отдельная функция ниже):
  1. start_purchase       — нажата кнопка «Зарегистрироваться»: бот фиксирует
                            текущую цену и просит перевести деньги и прислать чек
  2. handle_receipt       — пользователь прислал PDF-чек
  3. handle_phone_answer  — (только если нет username) пользователь прислал номер телефона

Между шагами состояние хранится в таблице pending_purchases (см. database.py) —
если бот перезапустится посреди диалога, пользователь просто продолжит с того
же места после следующего сообщения.
"""

import os
import re
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from config import RECEIVER_CONTRACT, ACCOUNT_LINK, GROUP_CHAT_ID
from database import (
    get_current_price,
    set_pending_purchase,
    get_pending_purchase,
    clear_pending_purchase,
    add_payment,
    mark_payment_sent,
    add_guest,
    receipt_exists,
)
from code_generator import generate_code
from receipt_parser import extract_text_from_pdf, parse_receipt_text, ReceiptParseError
from logger_setup import logger


# Тексты ошибок — собраны в одном месте, чтобы легко было поменять формулировку
ERROR_NOT_PDF = "Пришли чек файлом в формате PDF. Скриншот или фото не подойдут."
ERROR_UNKNOWN = "Не удалось обработать чек. Свяжись с организаторами, чтобы проверить оплату."
ERROR_STATUS = "В чеке перевод не отмечен как успешный. Проверь статус оплаты."
ERROR_RECEIVER = "Номер счёта получателя в чеке не совпадает с указанным для оплаты билета. Проверь реквизиты перевода."
ERROR_DUPLICATE = "Этот чек уже был обработан. Повторно использовать его для оформления билета нельзя."
ERROR_AMOUNT = "Сумма в чеке меньше стоимости билета. Свяжись с организаторами, чтобы уточнить дальнейшие действия."
ERROR_NOT_GROUP_MEMBER = "Регистрация через бота доступна только участникам нашей группы."

# Номер телефона в формате +7 123 456 78 90 (пробелы не обязательны)
PHONE_RE = re.compile(r"^\+7\s*\d{3}\s*\d{3}\s*\d{2}\s*\d{2}$")


async def is_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Проверяет через Telegram API, состоит ли пользователь в нужной группе."""
    try:
        member = await context.bot.get_chat_member(GROUP_CHAT_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Не удалось проверить членство в группе для {user_id}: {e}")
        return False


async def start_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Зарегистрироваться»."""
    user = update.effective_user

    if not await is_group_member(context, user.id):
        await update.message.reply_text(ERROR_NOT_GROUP_MEMBER)
        return

    existing = get_pending_purchase(user.id)

    # Старая версия бота спрашивала про детей и сохраняла этап "awaiting_kids".
    # Если у кого-то осталась такая незавершённая регистрация — просто начинаем её заново
    # (ниже), как будто её и не было.
    if existing is not None and existing["stage"] != "awaiting_kids":
        # У пользователя уже есть незавершённая регистрация — напоминаем, на чём остановились,
        # вместо того чтобы начинать всё заново и "терять" его место в процессе.
        if existing["stage"] == "awaiting_receipt":
            await update.message.reply_text(
                f"Ожидаю чек об оплате на сумму {existing['price_locked']} ₽. Пришли его файлом в формате PDF."
            )
        elif existing["stage"] == "awaiting_phone":
            await update.message.reply_text(
                "Для завершения оформления билета пришли номер телефона в формате +7 123 456 78 90."
            )
        return

    # Фиксируем цену в момент начала регистрации: если админ поменяет цену, пока человек
    # переводит деньги, сверяться будем с той суммой, которую бот ему назвал.
    price = get_current_price()
    set_pending_purchase(user.id, stage="awaiting_receipt", price_locked=price)

    await update.message.reply_text(
        f"Переведи {price} ₽ по следующим реквизитам: {ACCOUNT_LINK}. "
        f"Затем пришли чек файлом в формате PDF. Фото или скриншот не подойдут."
    )


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает присланный PDF-чек."""
    user = update.effective_user
    document = update.message.document

    pending = get_pending_purchase(user.id)
    if pending is None or pending["stage"] != "awaiting_receipt":
        await update.message.reply_text(
            "Чтобы начать регистрацию, сначала нажми кнопку «🎟 Зарегистрироваться»."
        )
        return

    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text(ERROR_NOT_PDF)
        return

    logger.info(f"Получен чек от user_id={user.id} (@{user.username}): {document.file_name}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = os.path.join(tmp_dir, document.file_name)
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(file_path)

        try:
            text = extract_text_from_pdf(file_path)
            parsed = parse_receipt_text(text)
        except ReceiptParseError as e:
            logger.warning(f"Не удалось распознать чек от {user.id}: {e}\n--- Текст чека ---\n{text}\n--- конец текста ---")
            await update.message.reply_text(ERROR_UNKNOWN)
            return
        except Exception as e:
            logger.error(f"Ошибка при чтении PDF от {user.id}: {e}")
            await update.message.reply_text(ERROR_UNKNOWN)
            return

    if parsed.status.lower() != "успешно":
        await update.message.reply_text(ERROR_STATUS)
        return

    if RECEIVER_CONTRACT and parsed.receiver_contract != RECEIVER_CONTRACT:
        await update.message.reply_text(ERROR_RECEIVER)
        return

    if receipt_exists(parsed.receipt_number):
        await update.message.reply_text(ERROR_DUPLICATE)
        return

    expected_amount = pending["price_locked"]
    if parsed.amount < expected_amount:
        logger.warning(
            f"Недостаточная сумма от {user.id}: пришло {parsed.amount}, ожидалось {expected_amount}"
        )
        await update.message.reply_text(ERROR_AMOUNT)
        return

    payment_id = add_payment(
        receipt_number=parsed.receipt_number,
        amount=parsed.amount,
        sender_user_id=user.id,
        sender_tag=user.username,
        price_used=pending["price_locked"],
    )

    if payment_id is None:
        await update.message.reply_text(ERROR_DUPLICATE)
        return

    if user.username:
        await _finalize_ticket(
            update, user_id=user.id, payment_id=payment_id,
            tag_or_phone=f"@{user.username}",
        )
        clear_pending_purchase(user.id)
    else:
        # Тега нет — просим телефон. Оплата уже записана (status='pending'),
        # код выдадим сразу после того, как получим номер.
        set_pending_purchase(
            user.id, stage="awaiting_phone",
            price_locked=pending["price_locked"], payment_id=payment_id,
        )
        await update.message.reply_text(
            "Чек принят. У тебя не указан username в Telegram, поэтому для оформления билета нужен номер телефона. "
            "Пришли его в формате +7 123 456 78 90."
        )


async def handle_phone_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, pending) -> None:
    """Обрабатывает присланный номер телефона (только если у пользователя нет username)."""
    text = update.message.text.strip()

    if not PHONE_RE.fullmatch(text):
        await update.message.reply_text(
            "Не удалось распознать номер телефона. Пришли его в формате +7 123 456 78 90."
        )
        return

    # Убираем пробелы — храним номер в едином формате
    phone = text.replace(" ", "")

    await _finalize_ticket(
        update, user_id=update.effective_user.id, payment_id=pending["payment_id"],
        tag_or_phone=phone,
    )
    clear_pending_purchase(update.effective_user.id)


async def _finalize_ticket(update: Update, user_id: int, payment_id: int, tag_or_phone: str) -> None:
    """Общая часть для двух сценариев (с username и с телефоном) — генерирует код и отвечает."""
    try:
        code = generate_code()
        add_guest(code=code, tag_or_phone=tag_or_phone, user_id=user_id, payment_id=payment_id)
        mark_payment_sent(payment_id)
    except Exception as e:
        logger.error(f"Ошибка при выдаче кода для payment_id={payment_id}: {e}")
        await update.message.reply_text(
            "Оплата принята, но не удалось выдать код входа. Свяжись с организаторами — они выдадут код вручную."
        )
        return

    await update.message.reply_text(
        f"Твой билет на Баскет Фест оформлен! Назови этот код на входе: {code}. "
        f"Данные билета: {tag_or_phone}.\n\n"
        f"Ждём тебя на нашем Баскет Фесте 26 декабря! 🏀"
    )
    logger.info(f"Код {code} выдан для payment_id={payment_id} ({tag_or_phone})")
