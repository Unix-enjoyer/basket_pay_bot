"""
Центральный диспетчер текстовых сообщений.

Каждое текстовое сообщение (не команда и не файл) проходит через эту
единственную функцию handle_text, которая решает, что с ним делать.
Порядок проверок важен — он построен от самого "узкого" случая к самому
"широкому", чтобы более специфичный сценарий не перебивался общим:

  1. У админа есть незавершённое действие (например, ждём новую цену)?
     -> это сообщение явно адресовано именно этому действию
  2. У пользователя есть незавершённая покупка (ждём число детей / телефон)?
     -> это сообщение явно продолжение покупки
  3. Текст совпадает с одной из кнопок?
     -> выполняем соответствующее действие
  4. Админ прислал что-то похожее на код гостя (3 буквы + 3 цифры)?
     -> это проверка на входе (работает всегда, без переключения режимов)
  5. Ничего не подошло -> молчим или мягко подсказываем, что делать
"""

import re

from telegram import Update
from telegram.ext import ContextTypes

from database import is_admin, get_admin_pending_action, get_pending_purchase
from handlers.keyboards import (
    BTN_BUY_TICKET, BTN_SET_PRICE, BTN_GUEST_LIST, BTN_ADD_GUEST, BTN_REMOVE_GUEST,
    BTN_ADD_ADMIN, BTN_REMOVE_ADMIN, BTN_ENTRY_INFO, BTN_STATS, BTN_CANCEL_LAST_CHECKIN,
    BTN_HELP, ADMIN_BUTTON_TEXTS,
)
from handlers.buyer import start_purchase, handle_kids_answer, handle_phone_answer
from handlers import admin as admin_handlers

# Формат кода: 3 латинские буквы + 3 цифры (см. code_generator.py)
CODE_PATTERN = re.compile(r"^[A-Za-z]{3}\d{3}$")

# Какая кнопка какую функцию запускает
BUTTON_HANDLERS = {
    BTN_SET_PRICE: admin_handlers.start_set_price,
    BTN_GUEST_LIST: admin_handlers.show_guest_list,
    BTN_ADD_GUEST: admin_handlers.start_add_guest,
    BTN_REMOVE_GUEST: admin_handlers.start_remove_guest,
    BTN_ADD_ADMIN: admin_handlers.show_add_admin_help,
    BTN_REMOVE_ADMIN: admin_handlers.show_remove_admin_help,
    BTN_ENTRY_INFO: admin_handlers.show_entry_info,
    BTN_STATS: admin_handlers.show_stats,
    BTN_CANCEL_LAST_CHECKIN: admin_handlers.cancel_last_checkin,
    BTN_HELP: admin_handlers.help_command,
}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text.strip()
    user_is_admin = is_admin(user.id)

    # --- 1. Незавершённое действие админа ---
    if user_is_admin:
        pending_action = get_admin_pending_action(user.id)
        if pending_action is not None:
            action = pending_action["action"]
            if action == "set_price":
                await admin_handlers.handle_set_price_answer(update, context)
                return
            if action == "add_guest_tag":
                await admin_handlers.handle_add_guest_tag_answer(update, context)
                return
            if action == "add_guest_comment_yn":
                await admin_handlers.handle_add_guest_comment_yn_answer(update, context, pending_action["temp_tag"])
                return
            if action == "add_guest_comment_text":
                await admin_handlers.handle_add_guest_comment_text_answer(update, context, pending_action["temp_tag"])
                return
            if action == "remove_guest":
                await admin_handlers.handle_remove_guest_answer(update, context)
                return

    # --- 2. Незавершённая покупка билета ---
    pending_purchase = get_pending_purchase(user.id)
    if pending_purchase is not None:
        if pending_purchase["stage"] == "awaiting_kids":
            await handle_kids_answer(update, context, pending_purchase)
            return
        if pending_purchase["stage"] == "awaiting_phone":
            await handle_phone_answer(update, context, pending_purchase)
            return
        # stage == "awaiting_receipt" ждёт файл, а не текст — сюда просто ничего не попадёт
        # осмысленного, упадём в пункт 5 (подсказка прислать PDF).

    # --- 3. Нажатие кнопки ---
    if text == BTN_BUY_TICKET:
        await start_purchase(update, context)
        return

    if user_is_admin and text in ADMIN_BUTTON_TEXTS:
        handler = BUTTON_HANDLERS[text]
        await handler(update, context)
        return

    # --- 4. Код гостя на входе (только у админов, работает всегда) ---
    if user_is_admin and CODE_PATTERN.fullmatch(text):
        await admin_handlers.handle_entry_code(update, context, text.upper())
        return

    # --- 5. Ничего не подошло ---
    if pending_purchase is not None and pending_purchase["stage"] == "awaiting_receipt":
        await update.message.reply_text("Жду от тебя файл чека в формате PDF.")
    # В остальных случаях молчим — чтобы не спамить людям в ответ на случайные сообщения
