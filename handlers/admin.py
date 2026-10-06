"""
Функции, доступные только администраторам бота.

Большинство действий теперь запускаются кнопкой и дальше идут диалогом
("кнопка → бот спрашивает → админ отвечает текстом"). Текущий шаг диалога
хранится в таблице admin_pending_actions (database.py) — функции из этого
файла, начинающиеся на handle_pending_action, вызываются из router.py,
когда у админа есть незавершённое действие.
"""

import asyncio

from telegram import Update, MessageOriginUser, MessageOriginHiddenUser
from telegram.ext import ContextTypes

from database import (
    is_admin,
    add_admin,
    remove_admin,
    set_price,
    get_all_guests,
    get_guests_with_user_id,
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
    await update.message.reply_text("Напиши новую цену билета в рублях — только число, например 1500.")


async def handle_set_price_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Укажи цену числом, например 1500. Попробуй ещё раз.")
        return

    new_price = int(text)
    set_price(new_price, changed_by=update.effective_user.id)
    clear_admin_pending_action(update.effective_user.id)
    await update.message.reply_text(f"Цена билета изменена: {new_price} ₽.")


# ==================== СПИСОК ОПЛАТИВШИХ ====================

@admin_only
async def show_guest_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guests = get_all_guests()
    if not guests:
        await update.message.reply_text("Пока нет оплаченных билетов.")
        return

    lines = []
    for g in guests:
        line = f"{g['code']} - {g['tag_or_phone']}"
        if g["comment"]:
            line += f" ({g['comment']})"
        lines.append(line)

    text = f"Список оплативших — {len(lines)} чел.:\n\n" + "\n".join(lines)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


# ==================== ДОБАВЛЕНИЕ ГОСТЯ ВРУЧНУЮ (с комментарием) ====================

@admin_only
async def start_add_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Добавить гостя»."""
    set_admin_pending_action(update.effective_user.id, action="add_guest_tag")
    await update.message.reply_text("Пришли username гостя в Telegram (например, @tag) или его номер телефона.")


async def handle_add_guest_tag_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tag_or_phone = update.message.text.strip()
    set_admin_pending_action(update.effective_user.id, action="add_guest_comment_yn", temp_tag=tag_or_phone)
    await update.message.reply_text("Добавить комментарий к записи гостя?", reply_markup=YES_NO_KEYBOARD)


async def handle_add_guest_comment_yn_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, temp_tag: str) -> None:
    text = update.message.text.strip()

    if text == BTN_YES:
        set_admin_pending_action(update.effective_user.id, action="add_guest_comment_text", temp_tag=temp_tag)
        await update.message.reply_text("Напиши комментарий к записи гостя.")
        return

    if text == BTN_NO:
        await _finish_add_guest(update, tag_or_phone=temp_tag, comment=None)
        return

    await update.message.reply_text("Нажми кнопку «Да» или «Нет».", reply_markup=YES_NO_KEYBOARD)


async def handle_add_guest_comment_text_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, temp_tag: str) -> None:
    comment = update.message.text.strip()
    await _finish_add_guest(update, tag_or_phone=temp_tag, comment=comment)


async def _finish_add_guest(update: Update, tag_or_phone: str, comment) -> None:
    code = generate_code()
    add_guest(code=code, tag_or_phone=tag_or_phone, added_by_admin=update.effective_user.id, comment=comment)
    clear_admin_pending_action(update.effective_user.id)

    text = f"Гость добавлен вручную. Код входа: {code}. Данные гостя: {tag_or_phone}."
    if comment:
        text += f"\nКомментарий: {comment}"
    await update.message.reply_text(text, reply_markup=ADMIN_KEYBOARD)


# ==================== УДАЛЕНИЕ ГОСТЯ ====================

@admin_only
async def start_remove_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_admin_pending_action(update.effective_user.id, action="remove_guest")
    await update.message.reply_text("Пришли код гостя, которого нужно удалить из списка.")


async def handle_remove_guest_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = update.message.text.strip().upper()
    success = remove_guest(code)
    clear_admin_pending_action(update.effective_user.id)

    if success:
        await update.message.reply_text(f"Гость с кодом {code} удалён из списка.")
    else:
        await update.message.reply_text(f"Гость с кодом {code} не найден.")


# ==================== ДОБАВЛЕНИЕ / СНЯТИЕ АДМИНА ====================
# Эти команды остаются текстовыми командами (/add_admin, /remove_admin),
# т.к. им обязательно нужен reply на пересланное сообщение — кнопкой это не сделать.

@admin_only
async def show_add_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Добавить админа» — просто показываем инструкцию."""
    await update.message.reply_text(
        "Чтобы назначить администратора, перешли сюда любое сообщение этого пользователя. "
        "Затем ответь на пересланное сообщение командой /add_admin.\n\n"
        "Если пересылка не работает (у человека скрыт автор пересылки), попроси его написать "
        "боту команду /myid и пришли мне полученное число: /add_admin 123456789"
    )


@admin_only
async def show_remove_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Чтобы снять права администратора, перешли сюда любое сообщение этого пользователя. "
        "Затем ответь на пересланное сообщение командой /remove_admin."
    )


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Запасной способ: /add_admin 123456789 — когда пересылка не работает
    # из-за настроек приватности. Свой ID человек узнаёт командой /myid.
    if context.args:
        if not context.args[0].isdigit():
            await update.message.reply_text("ID должен состоять только из цифр. Пример: /add_admin 123456789")
            return
        new_id = int(context.args[0])
        add_admin(new_id, tag=None, added_by=update.effective_user.id)
        logger.info(f"Админ {new_id} назначен по ID пользователем {update.effective_user.id}")
        await update.message.reply_text(f"Пользователь с ID {new_id} назначен администратором.")
        return

    replied = update.message.reply_to_message
    if replied is None or replied.forward_origin is None:
        await show_add_admin_help(update, context)
        return

    origin = replied.forward_origin

    if isinstance(origin, MessageOriginHiddenUser):
        await update.message.reply_text(
            "Не удалось определить пользователя: его настройки приватности скрывают автора пересланных сообщений. "
            "Попроси его разрешить отображение автора при пересылке и попробуй снова."
        )
        return

    if not isinstance(origin, MessageOriginUser):
        await update.message.reply_text(
            "Не удалось определить пользователя по пересланному сообщению. Перешли сообщение, отправленное от личного аккаунта."
        )
        return

    new_admin = origin.sender_user
    add_admin(new_admin.id, tag=new_admin.username, added_by=update.effective_user.id)
    await update.message.reply_text(f"Пользователь @{new_admin.username} назначен администратором.")


@admin_only
async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Запасной способ: /remove_admin 123456789
    if context.args:
        if not context.args[0].isdigit():
            await update.message.reply_text("ID должен состоять только из цифр. Пример: /remove_admin 123456789")
            return
        if remove_admin(int(context.args[0])):
            await update.message.reply_text(f"У пользователя с ID {context.args[0]} сняты права администратора.")
        else:
            await update.message.reply_text("Снять права главного администратора нельзя.")
        return

    replied = update.message.reply_to_message
    if replied is None or replied.forward_origin is None:
        await show_remove_admin_help(update, context)
        return

    origin = replied.forward_origin

    if isinstance(origin, MessageOriginHiddenUser):
        await update.message.reply_text(
            "Не удалось определить пользователя: его настройки приватности скрывают автора пересланных сообщений."
        )
        return

    if not isinstance(origin, MessageOriginUser):
        await update.message.reply_text(
            "Не удалось определить пользователя по пересланному сообщению. Перешли сообщение, отправленное от личного аккаунта."
        )
        return

    target = origin.sender_user
    success = remove_admin(target.id)
    if success:
        await update.message.reply_text(f"У пользователя @{target.username} сняты права администратора.")
    else:
        await update.message.reply_text("Снять права главного администратора нельзя.")


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myid — любой пользователь узнаёт свой числовой ID (нужен, чтобы назначить его админом)."""
    await update.message.reply_text(f"Твой ID: {update.effective_user.id}")


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
        await update.message.reply_text("Гость с таким кодом не найден в списке.")
        return

    if result["already"]:
        await update.message.reply_text(
            f"По этому коду вход уже отмечен. Время входа: {result['checked_in_at']}."
        )
        return

    await update.message.reply_text(f"Гость {result['tag_or_phone']} найден. Вход отмечен. Добро пожаловать на Баскет Фест!")


@admin_only
async def cancel_last_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Отменить последний вход» — отменяет именно последний вход ЭТОГО админа."""
    admin_id = update.effective_user.id
    last = get_last_checkin_by_admin(admin_id)

    if last is None:
        await update.message.reply_text("Нет отмеченных тобой входов, которые можно отменить.")
        return

    cancel_checkin(last["code"])
    await update.message.reply_text(
        f"Отметка о входе отменена: {last['code']} — {last['tag_or_phone']}. Гость больше не отмечен как вошедший."
    )


# ==================== РАССЫЛКА ОПЛАТИВШИМ ====================

# Небольшая пауза между отправками, чтобы не упереться в лимит Telegram
# (~30 сообщений в секунду разным людям). 0.05 сек = 20 сообщений в секунду — с запасом.
BROADCAST_DELAY_SECONDS = 0.05


@admin_only
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Нажата кнопка «Уведомление»."""
    set_admin_pending_action(update.effective_user.id, action="broadcast_message")
    await update.message.reply_text(
        "Напиши сообщение для рассылки всем оплатившим. "
        "В конце каждого сообщения автоматически появится напоминание с кодом входа получателя."
    )


async def handle_broadcast_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_text = update.message.text.strip()
    clear_admin_pending_action(update.effective_user.id)

    # Берём только тех, кому можно написать напрямую — то есть кто сам покупал билет через бота.
    # Гости, добавленные вручную по тегу/телефону, сюда не попадают: бот не знает их Telegram-аккаунт.
    guests = get_guests_with_user_id()
    all_guests = get_all_guests()
    skipped_manual = len(all_guests) - len(guests)

    if not guests:
        await update.message.reply_text("Пока нет оплативших, которым бот может отправить сообщение напрямую.")
        return

    await update.message.reply_text(f"Начинаю рассылку. Получателей: {len(guests)}. Отправка может занять некоторое время.")

    sent = 0
    failed = 0
    for guest in guests:
        personal_text = f"{message_text}\n\nНапоминаю, твой код для входа — {guest['code']}"
        try:
            await context.bot.send_message(chat_id=guest["user_id"], text=personal_text)
            sent += 1
        except Exception as e:
            # Частая причина — человек заблокировал бота после оплаты.
            # Не прерываем всю рассылку из-за одного неудачного отправления.
            failed += 1
            logger.warning(f"Не удалось отправить уведомление {guest['user_id']} ({guest['tag_or_phone']}): {e}")

        await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    report = f"Рассылка завершена. Отправлено: {sent}. Не удалось отправить: {failed}."
    if skipped_manual:
        report += f"\nПропущены гости, добавленные вручную: {skipped_manual}."
    await update.message.reply_text(report)


# ==================== СТАТИСТИКА ====================

@admin_only
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = get_attendance_stats()

    lines = [f"Пришли: {stats['checked_in_count']} из {stats['total']} гостей.", ""]

    not_came = stats["not_checked_in"]
    lines.append("Не пришли: " + (", ".join(not_came) if not_came else "все пришли 🎉"))
    lines.append("")

    for price in sorted(stats["price_breakdown"].keys()):
        count = stats["price_breakdown"][price]
        lines.append(f"Билеты по цене {price} ₽: {count} чел.")

    if stats["manual_count"]:
        lines.append(f"Добавлены вручную: {stats['manual_count']} чел.")

    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


# ==================== СПРАВКА ====================

@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ADMIN_HELP_TEXT)
