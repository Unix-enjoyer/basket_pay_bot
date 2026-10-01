"""
Функции, доступные только администраторам бота.

Большинство действий теперь запускаются кнопкой и дальше идут диалогом
("кнопка → бот спрашивает → админ отвечает текстом"). Текущий шаг диалога
хранится в таблице admin_pending_actions (database.py) — функции из этого
файла, начинающиеся на handle_pending_action, вызываются из router.py,
когда у админа есть незавершённое действие.
"""

from telegram import Update, MessageOriginUser, MessageOriginHiddenUser
from telegram.ext import ContextTypes

from database import (
    is_admin,
    add_admin,
    remove_admin,
    set_price,
    get_all_guests,
    remove_guest,
    add_guest,
    set_admin_pending_action,
    clear_admin_pending_action,
    check_in_guest,
    get_last_checkin_by_admin,
    cancel_checkin,
    get_attendance_stats,
)
from code_generator import generate_code
from handlers.keyboards import (
    ADMIN_HELP_TEXT, ENTRY_INFO_TEXT, YES_NO_KEYBOARD, ADMIN_KEYBOARD,
    BTN_YES, BTN_NO,
)
from logger_setup import logger


def admin_only(func):
    """Не даёт выполнить действие, если его вызвал не администратор."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("Эта функция доступна только администраторам.")
            return
        return await func(update, context)
    return wrapper


# ==================== ИЗМЕНЕНИЕ ЦЕНЫ ====================

@admin_only
async def start_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Изменить цену»."""
    set_admin_pending_action(update.effective_user.id, action="set_price")
    await update.message.reply_text("Напиши новую цену билета (только число, например 1500).")


async def handle_set_price_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Нужно просто число, например 1500. Попробуй ещё раз.")
        return

    new_price = int(text)
    set_price(new_price, changed_by=update.effective_user.id)
    clear_admin_pending_action(update.effective_user.id)
    await update.message.reply_text(f"Новая цена билета: {new_price} ₽")


# ==================== СПИСОК ОПЛАТИВШИХ ====================

@admin_only
async def show_guest_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guests = get_all_guests()
    if not guests:
        await update.message.reply_text("Пока никто не оплатил.")
        return

    lines = []
    for g in guests:
        line = f"{g['code']} - {g['tag_or_phone']}"
        if g["kids_count"]:
            line += f" {g['kids_count']}дет"
        if g["comment"]:
            line += f" ({g['comment']})"
        lines.append(line)

    text = f"Список оплативших ({len(lines)} чел.):\n\n" + "\n".join(lines)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


# ==================== ДОБАВЛЕНИЕ ГОСТЯ ВРУЧНУЮ (с комментарием) ====================

@admin_only
async def start_add_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Добавить гостя»."""
    set_admin_pending_action(update.effective_user.id, action="add_guest_tag")
    await update.message.reply_text("Тег (@tag) или номер телефона гостя?")


async def handle_add_guest_tag_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tag_or_phone = update.message.text.strip()
    set_admin_pending_action(update.effective_user.id, action="add_guest_comment_yn", temp_tag=tag_or_phone)
    await update.message.reply_text("Добавить комментарий к гостю?", reply_markup=YES_NO_KEYBOARD)


async def handle_add_guest_comment_yn_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, temp_tag: str) -> None:
    text = update.message.text.strip()

    if text == BTN_YES:
        set_admin_pending_action(update.effective_user.id, action="add_guest_comment_text", temp_tag=temp_tag)
        await update.message.reply_text("Напиши комментарий.")
        return

    if text == BTN_NO:
        await _finish_add_guest(update, tag_or_phone=temp_tag, comment=None)
        return

    await update.message.reply_text("Выбери «Да» или «Нет».", reply_markup=YES_NO_KEYBOARD)


async def handle_add_guest_comment_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, temp_tag: str) -> None:
    comment = update.message.text.strip()
    await _finish_add_guest(update, tag_or_phone=temp_tag, comment=comment)


async def _finish_add_guest(update: Update, tag_or_phone: str, comment) -> None:
    code = generate_code()
    add_guest(code=code, tag_or_phone=tag_or_phone, added_by_admin=update.effective_user.id, comment=comment)
    clear_admin_pending_action(update.effective_user.id)

    text = f"Гость добавлен вручную:\n{code} - {tag_or_phone}"
    if comment:
        text += f"\nКомментарий: {comment}"
    await update.message.reply_text(text, reply_markup=ADMIN_KEYBOARD)


# ==================== УДАЛЕНИЕ ГОСТЯ ====================

@admin_only
async def start_remove_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_admin_pending_action(update.effective_user.id, action="remove_guest")
    await update.message.reply_text("Код гостя для удаления?")


async def handle_remove_guest_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    success = remove_guest(code)
    clear_admin_pending_action(update.effective_user.id)

    if success:
        await update.message.reply_text(f"Гость с кодом {code} удалён.")
    else:
        await update.message.reply_text(f"Код {code} не найден.")


# ==================== ДОБАВЛЕНИЕ / СНЯТИЕ АДМИНА ====================
# Эти команды остаются текстовыми командами (/add_admin, /remove_admin),
# т.к. им обязательно нужен reply на пересланное сообщение — кнопкой это не сделать.

@admin_only
async def show_add_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Добавить админа» — просто показываем инструкцию."""
    await update.message.reply_text(
        "Чтобы назначить админа:\n"
        "1) Перешли (Forward) сюда любое сообщение нужного человека\n"
        "2) Ответь (Reply) на это пересланное сообщение командой /add_admin"
    )


@admin_only
async def show_remove_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Чтобы снять админа:\n"
        "1) Перешли (Forward) сюда любое его сообщение\n"
        "2) Ответь (Reply) на это пересланное сообщение командой /remove_admin"
    )


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    replied = update.message.reply_to_message
    if replied is None or replied.forward_origin is None:
        await show_add_admin_help(update, context)
        return

    origin = replied.forward_origin

    if isinstance(origin, MessageOriginHiddenUser):
        await update.message.reply_text(
            "Не удалось определить личность — у этого человека скрыт автор при пересылке. "
            "Попроси его самого написать боту /start, а затем попробуй снова."
        )
        return

    if not isinstance(origin, MessageOriginUser):
        await update.message.reply_text("Не удалось определить личность из пересланного сообщения.")
        return

    new_admin = origin.sender_user
    add_admin(new_admin.id, tag=new_admin.username, added_by=update.effective_user.id)
    await update.message.reply_text(f"Пользователь @{new_admin.username} назначен администратором.")


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    replied = update.message.reply_to_message
    if replied is None or replied.forward_origin is None:
        await show_remove_admin_help(update, context)
        return

    origin = replied.forward_origin

    if isinstance(origin, MessageOriginHiddenUser):
        await update.message.reply_text("Не удалось определить личность — у этого человека скрыт автор при пересылке.")
        return

    if not isinstance(origin, MessageOriginUser):
        await update.message.reply_text("Не удалось определить личность из пересланного сообщения.")
        return

    target = origin.sender_user
    success = remove_admin(target.id)
    if success:
        await update.message.reply_text(f"Пользователь @{target.username} больше не администратор.")
    else:
        await update.message.reply_text("Нельзя снять супер-админа.")


# ==================== ВХОД НА ТУРНИР ====================

@admin_only
async def show_entry_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Вход» — просто инструкция, никакого отдельного режима включать не нужно."""
    await update.message.reply_text(ENTRY_INFO_TEXT)


async def handle_entry_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """
    Вызывается из router.py, когда админ прислал текст, похожий на код (3 буквы + 3 цифры).
    Это работает ВСЕГДА, параллельно со всеми остальными кнопками — отдельного режима нет.
    """
    admin_id = update.effective_user.id
    result = check_in_guest(code, admin_id=admin_id)

    if result is None:
        await update.message.reply_text("Ой, этого гостя нет в списках...")
        return

    if result["already"]:
        await update.message.reply_text(
            f"Ой, по этому коду уже кто-то зашёл — время входа: {result['checked_in_at']}"
        )
        return

    await update.message.reply_text(f"О, так это же гость {result['tag_or_phone']}! Милости просим!")


@admin_only
async def cancel_last_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Отменить последний вход» — отменяет именно последний вход ЭТОГО админа."""
    admin_id = update.effective_user.id
    last = get_last_checkin_by_admin(admin_id)

    if last is None:
        await update.message.reply_text("Нет отмеченных тобой входов, которые можно было бы отменить.")
        return

    cancel_checkin(last["code"])
    await update.message.reply_text(
        f"Отменено: {last['code']} - {last['tag_or_phone']}. Этот гость больше не отмечен как прошедший."
    )


# ==================== СТАТИСТИКА ====================

@admin_only
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = get_attendance_stats()

    lines = [f"Итог — пришло {stats['checked_in_count']} гостей из {stats['total']}", ""]

    not_came = stats["not_checked_in"]
    lines.append("Не пришли: " + (", ".join(not_came) if not_came else "все пришли 🎉"))
    lines.append("")

    for price in sorted(stats["price_breakdown"].keys()):
        count = stats["price_breakdown"][price]
        lines.append(f"По цене {price}₽ оплатило {count} человек")

    if stats["manual_count"]:
        lines.append(f"Добавлено вручную: {stats['manual_count']} человек")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


# ==================== СПРАВКА ====================

@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ADMIN_HELP_TEXT)
