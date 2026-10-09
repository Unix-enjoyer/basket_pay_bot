"""
Тексты кнопок и готовые клавиатуры.

Кнопка в Telegram — это просто способ быстро отправить готовый текст, не
печатая его руками. Поэтому текст каждой кнопки хранится тут как константа
и используется в двух местах: при построении клавиатуры и при распознавании
нажатия в handlers/router.py (там просто сравнивается пришедший текст с
этими константами).
"""

from telegram import ReplyKeyboardMarkup

# --- Кнопка участника ---
BTN_BUY_TICKET = "🎟 Зарегистрироваться"

# Прежний текст этой кнопки. У людей, которые уже открывали бота, на телефоне может
# остаться старая клавиатура — бот продолжает понимать и её, пока они не нажмут /start.
LEGACY_BUY_TICKET_TEXTS = {"🎟 Купить билет себе"}

# --- Кнопки администратора ---
BTN_SET_PRICE = "💰 Изменить цену"
BTN_GUEST_LIST = "📋 Список оплативших"
BTN_ADD_GUEST = "➕ Добавить гостя"
BTN_REMOVE_GUEST = "➖ Удалить гостя"
BTN_ADD_ADMIN = "👤 Добавить админа"
BTN_REMOVE_ADMIN = "🚫 Удалить админа"
BTN_ADMIN_LIST = "👥 Список админов"
BTN_STATS = "📊 Статистика по приходу"
BTN_CANCEL_LAST_CHECKIN = "↩️ Отменить последний вход"
BTN_BROADCAST = "📢 Уведомление"
BTN_HELP = "ℹ️ Список команд"

# --- Кнопки да/нет (используются в диалоге добавления гостя) ---
BTN_YES = "Да"
BTN_NO = "Нет"

ADMIN_HELP_TEXT = (
    "📋 Кнопки администратора:\n\n"
    f"{BTN_SET_PRICE} — установить новую цену билета\n"
    f"{BTN_GUEST_LIST} — список всех, кому выданы коды\n"
    f"{BTN_ADD_GUEST} — добавить гостя вручную (без чека)\n"
    f"{BTN_REMOVE_GUEST} — удалить гостя по коду\n"
    f"{BTN_ADD_ADMIN} / {BTN_REMOVE_ADMIN} — назначить/снять админа (перешли сообщение человека + reply командой, либо /add_admin ID и /remove_admin ID)\n"
    f"{BTN_ADMIN_LIST} — показать всех администраторов\n"
    f"{BTN_STATS} — итоговая статистика по приходу\n"
    f"{BTN_CANCEL_LAST_CHECKIN} — отменить свой последний отмеченный вход (если ошиблись)\n"
    f"{BTN_BROADCAST} — разослать сообщение всем, кто оплатил билет (с напоминанием кода каждому)\n\n"
    "🏀 Коды гостей на входе можно присылать в любой момент — просто напиши код "
    "(например ABC123), бот сам распознает его и проверит."
)

ENTRY_INFO_TEXT = (
    "Чтобы отметить вход гостя на Баскет Фест, пришли его код. "
    "Код можно отправить в любой момент — включать отдельный режим не нужно."
)

# Клавиатура для обычного пользователя (не админа)
BUYER_KEYBOARD = ReplyKeyboardMarkup([[BTN_BUY_TICKET]], resize_keyboard=True)

# Клавиатура для администратора — включает и кнопку регистрации для себя тоже
ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_BUY_TICKET],
        [BTN_GUEST_LIST, BTN_STATS],
        [BTN_SET_PRICE, BTN_ADD_GUEST],
        [BTN_REMOVE_GUEST, BTN_CANCEL_LAST_CHECKIN],
        [BTN_ADD_ADMIN, BTN_REMOVE_ADMIN],
        [BTN_ADMIN_LIST, BTN_HELP],
        [BTN_BROADCAST],
    ],
    resize_keyboard=True,
)

# Клавиатура да/нет — показывается во время диалога добавления гостя
YES_NO_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_YES, BTN_NO]], resize_keyboard=True, one_time_keyboard=True
)

# Множество всех текстов кнопок админа — удобно для проверки "это вообще кнопка?"
ADMIN_BUTTON_TEXTS = {
    BTN_SET_PRICE, BTN_GUEST_LIST, BTN_ADD_GUEST, BTN_REMOVE_GUEST,
    BTN_ADD_ADMIN, BTN_REMOVE_ADMIN, BTN_ADMIN_LIST, BTN_STATS,
    BTN_CANCEL_LAST_CHECKIN, BTN_HELP, BTN_BROADCAST,
}
