"""
Команды, доступные только администраторам бота.
"""

from telegram import Update
from telegram.ext import ContextTypes

from database import (
    is_admin,
    add_admin,
    remove_admin,
    set_price,
    get_all_guests,
    remove_guest,
    add_guest,
)
from code_generator import generate_code
from handlers.keyboards import ADMIN_HELP_TEXT


def admin_only(func):
    """
    Декоратор: не даёт выполнить команду, если её вызвал не администратор.
    Оборачиваем им каждую admin-команду ниже, чтобы не копировать одну и ту же
    проверку в каждой функции.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("Эта команда доступна только администраторам.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def set_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_price 1500 — устанавливает новую цену билета."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Использование: /set_price <сумма в рублях>\nНапример: /set_price 1500"
        )
        return

    new_price = int(context.args[0])
    set_price(new_price, changed_by=update.effective_user.id)
    await update.message.reply_text(f"Новая цена билета: {new_price} ₽")


@admin_only
async def check_guest_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/check_guest_list — показывает список всех, кому выданы коды."""
    guests = get_all_guests()
    if not guests:
        await update.message.reply_text("Пока никто не оплатил.")
        return

    lines = [f"{g['code']} - {g['tag_or_phone']}" for g in guests]
    text = f"Список оплативших ({len(lines)} чел.):\n\n" + "\n".join(lines)

    # Telegram ограничивает длину одного сообщения ~4096 символами,
    # поэтому при большом списке разбиваем его на несколько сообщений
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /add_admin — нужно вызвать ОТВЕТОМ (reply) на сообщение того человека,
    кого вы хотите назначить админом. Так Telegram гарантированно даёт нам
    его user_id — по одному только @тегу это сделать нельзя, если человек
    ни разу не писал боту.
    """
    replied = update.message.reply_to_message
    if replied is None or replied.from_user is None:
        await update.message.reply_text(
            "Чтобы назначить админа, ответьте этой командой (Reply) на любое "
            "сообщение нужного человека в этом чате. Человек должен был хотя бы раз написать боту."
        )
        return

    new_admin = replied.from_user
    add_admin(new_admin.id, tag=new_admin.username, added_by=update.effective_user.id)
    await update.message.reply_text(f"Пользователь @{new_admin.username} назначен администратором.")


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remove_admin — ответом на сообщение админа, которого нужно снять."""
    replied = update.message.reply_to_message
    if replied is None or replied.from_user is None:
        await update.message.reply_text(
            "Чтобы снять админа, ответьте этой командой (Reply) на любое его сообщение в этом чате."
        )
        return

    target = replied.from_user
    success = remove_admin(target.id)
    if success:
        await update.message.reply_text(f"Пользователь @{target.username} больше не администратор.")
    else:
        await update.message.reply_text("Нельзя снять супер-админа.")


@admin_only
async def add_guest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /add_guest @tag  (или /add_guest +79123456789)
    Добавляет гостя вручную, без чека — например, если человек заплатил наличными.
    """
    if not context.args:
        await update.message.reply_text(
            "Использование: /add_guest @тег  (или номер телефона +79123456789)"
        )
        return

    tag_or_phone = context.args[0]
    code = generate_code()
    add_guest(code=code, tag_or_phone=tag_or_phone, added_by_admin=update.effective_user.id)
    await update.message.reply_text(f"Гость добавлен вручную:\n{code} - {tag_or_phone}")


@admin_only
async def remove_guest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remove_guest ABC123 — удаляет гостя по коду входа."""
    if not context.args:
        await update.message.reply_text(
            "Использование: /remove_guest <код>\nНапример: /remove_guest ABC123"
        )
        return

    code = context.args[0].upper()
    success = remove_guest(code)
    if success:
        await update.message.reply_text(f"Гость с кодом {code} удалён.")
    else:
        await update.message.reply_text(f"Код {code} не найден.")


@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help или кнопка «Список команд» — показывает памятку по admin-командам."""
    await update.message.reply_text(ADMIN_HELP_TEXT)
