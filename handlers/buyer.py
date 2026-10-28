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

import html
import os
import re
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from telegram.ext import ContextTypes

from config import (
    RECEIVER_CONTRACT, ACCOUNT_LINK, GROUP_CHAT_ID,
    PAYMENT_QR_PATH, ORGANIZERS, RECEIVER_NAME, PAYMENT_PHONE
)
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


# Список организаторов из .env, вставляется в реплики вида "Свяжись с организаторами (@ivan, @olga)".
# Если ORGANIZERS в .env пуст — получится просто "Свяжись с организаторами".
ORGANIZERS_TEXT = f" ({ORGANIZERS})" if ORGANIZERS else ""

# Если организаторов несколько (в ORGANIZERS есть запятая) — просим писать только одному
ORGANIZERS_NOTE = (
    "\n\nПожалуйста, не пиши всем организаторам сразу, а только одному. Они постараются ответить как можно быстрее."
    if "," in ORGANIZERS else ""
)

# Тексты ошибок — собраны в одном месте, чтобы легко было поменять формулировку
ERROR_NOT_PDF = "⚠️ Пришли чек файлом в формате PDF. Скриншот или фото не подойдут."
ERROR_UNKNOWN = f"⚠️ Не удалось обработать чек. Свяжись с организаторами{ORGANIZERS_TEXT}, чтобы проверить оплату." + ORGANIZERS_NOTE
ERROR_STATUS = "⚠️ В чеке перевод не отмечен как успешный. Проверь статус оплаты."
ERROR_RECEIVER = "⚠️ Номер счёта получателя в чеке не совпадает с указанным для оплаты билета. Проверь реквизиты перевода."
ERROR_DUPLICATE = "⚠️ Этот чек уже был обработан. Повторно использовать его для оформления билета нельзя."
ERROR_AMOUNT = f"⚠️ Сумма в чеке меньше стоимости билета. Свяжись с организаторами{ORGANIZERS_TEXT}, чтобы уточнить дальнейшие действия." + ORGANIZERS_NOTE
ERROR_NOT_GROUP_MEMBER = "⚠️ Регистрация через бота доступна только участникам нашей группы."

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


def build_copy_keyboard():
    """Кнопка «Скопировать номер договора» под сообщением с реквизитами.

    Нажатие копирует номер в буфер обмена прямо в Telegram, боту ничего не приходит.
    Номер берётся из RECEIVER_CONTRACT (.env). Если он пуст — кнопки нет.
    """
    if not RECEIVER_CONTRACT:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Скопировать номер договора", copy_text=CopyTextButton(text=RECEIVER_CONTRACT))
    ]])


def build_payment_text(price: int, reminder: bool = False) -> str:
    """Собирает текст сообщения с реквизитами (HTML-разметка: жирный шрифт через <b>).

    Всё, что приходит из .env, пропускаем через html.escape, чтобы символы < > & не ломали разметку.
    Строки, значений для которых в .env нет, просто не показываются.
    """
    esc = html.escape
    blocks = []

    if reminder:
        blocks.append(f"⏳ Ожидаю чек об оплате на сумму {price} ₽.")

    blocks.append(f"💳 <b>Оплата — {price} ₽</b>")

    blocks.append(f"❤️ Хочешь дополнительно поддержать фестиваль? Можешь перевести больше {price} ₽. Будем благодарны за любую поддержку! 🏀")

    if ACCOUNT_LINK:
        blocks.append(f"🔗 <b>По ссылке:</b>\n{esc(ACCOUNT_LINK)}")

    requisites = []
    if RECEIVER_NAME:
        requisites.append(f"Получатель: {esc(RECEIVER_NAME)}")
    if RECEIVER_CONTRACT:
        requisites.append(f"По договору № {esc(RECEIVER_CONTRACT)}")
    if requisites:
        blocks.append("🏦 <b>По реквизитам Т-Банка:</b>\n" + "\n".join(requisites))

    blocks.append("📄 <b>После оплаты отправь сюда чек в формате PDF. Фото и скриншоты бот не принимает.</b>")

    blocks.append("⚠️ <b>ВАЖНО!</b>\n<b>Бот автоматически распознаёт только чеки Т-Банка!</b>")

    # Организаторы из .env (через запятую) выводим каждого с новой строки
    organizers = [o.strip() for o in ORGANIZERS.split(",") if o.strip()]
    if PAYMENT_PHONE:
        phone_text = (
            "📱 <b>Если оплачиваешь через другой банк, переведи по номеру телефона:</b>\n"
            f"{esc(PAYMENT_PHONE)}\n"
        )
        if organizers:
            phone_text += "Затем отправь чек одному из организаторов:\n\n" + "\n".join(esc(o) for o in organizers)
        else:
            phone_text += "Затем отправь чек организаторам."
        blocks.append(phone_text)
    elif organizers:
        blocks.append(
            "Если оплачиваешь через другой банк, отправь чек одному из организаторов:\n\n"
            + "\n".join(esc(o) for o in organizers)
        )
    else:
        blocks.append("Если оплачиваешь через другой банк, свяжись с организаторами.")

    blocks.append("Организатор вручную подтвердит оплату и добавит тебя в список гостей.")

    if len(organizers) > 1:
        blocks.append(
            "🙏 <b>Пожалуйста, обращайся только к одному организатору, чтобы не дублировать запросы. "
            "Мы постараемся ответить как можно быстрее!</b>"
        )

    blocks.append("🏀 Спасибо! До встречи на фестивале!")

    return "\n\n".join(blocks)


async def send_payment_info(update: Update, price: int, reminder: bool = False) -> None:
    """Отправляет реквизиты для оплаты: QR-картинку (если она есть) с текстом в подписи.

    reminder=False — первое сообщение после нажатия кнопки,
    reminder=True  — напоминание тем, кто нажал кнопку повторно.
    """
    text = build_payment_text(price, reminder)
    keyboard = build_copy_keyboard()

    # Подпись к фото в Telegram — не длиннее 1024 символов (считаются символы без HTML-тегов).
    plain_len = len(html.unescape(re.sub(r"<[^>]+>", "", text)))

    # Если QR-файл задан и существует — шлём фото.
    if PAYMENT_QR_PATH and os.path.isfile(PAYMENT_QR_PATH):
        try:
            with open(PAYMENT_QR_PATH, "rb") as qr_file:
                if plain_len <= 1024:
                    await update.message.reply_photo(
                        photo=qr_file, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
                else:
                    await update.message.reply_photo(photo=qr_file)
                    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception as e:
            logger.error(f"Не удалось отправить QR-код ({PAYMENT_QR_PATH}): {e}")

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


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
            await send_payment_info(update, existing["price_locked"], reminder=True)
        elif existing["stage"] == "awaiting_phone":
            await update.message.reply_text(
                "📱 Для завершения оформления билета пришли номер телефона в формате +7 123 456 78 90."
            )
        return

    # Фиксируем цену в момент начала регистрации: если админ поменяет цену, пока человек
    # переводит деньги, сверяться будем с той суммой, которую бот ему назвал.
    price = get_current_price()
    set_pending_purchase(user.id, stage="awaiting_receipt", price_locked=price)

    await send_payment_info(update, price)


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает присланный PDF-чек."""
    user = update.effective_user
    document = update.message.document

    pending = get_pending_purchase(user.id)
    if pending is None or pending["stage"] != "awaiting_receipt":
        await update.message.reply_text(
            "🎟 Чтобы начать регистрацию, сначала нажми кнопку «🎟 Зарегистрироваться»."
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
            "✅ Чек принят! У тебя не указан username в Telegram, поэтому для оформления билета нужен номер телефона.\n\n"
            "📱 Пришли его в формате +7 123 456 78 90."
        )


async def handle_phone_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, pending) -> None:
    """Обрабатывает присланный номер телефона (только если у пользователя нет username)."""
    text = update.message.text.strip()

    if not PHONE_RE.fullmatch(text):
        await update.message.reply_text(
            "⚠️ Не удалось распознать номер телефона. Пришли его в формате +7 123 456 78 90."
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
            f"⚠️ Оплата принята, но не удалось выдать код входа. Свяжись с организаторами{ORGANIZERS_TEXT} — они выдадут код вручную." + ORGANIZERS_NOTE
        )
        return

    await update.message.reply_text(
        f"🎉 Твой билет на Баскет Фест оформлен!\n\n"
        f"🎟 Назови этот код на входе: {code}\n"
        f"📋 Данные билета: {tag_or_phone}\n\n"
        f"Ждём тебя на нашем Баскет Фесте 26 декабря! 🏀"
    )
    logger.info(f"Код {code} выдан для payment_id={payment_id} ({tag_or_phone})")
