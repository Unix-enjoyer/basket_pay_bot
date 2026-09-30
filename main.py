"""
Точка входа в приложение.

Что происходит при запуске (по шагам):
  1. Инициализируем базу данных (создаём таблицы, если их нет)
  2. Проверяем, не остались ли с прошлого запуска "зависшие" оплаты
     (статус 'pending') — если да, шлём супер-админу список для ручной проверки
  3. Регистрируем все обработчики команд и сообщений
  4. Запускаем бота в режиме polling (бот сам опрашивает Telegram на новые сообщения)

Запуск: python main.py
"""

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, SUPER_ADMIN_ID
from database import init_db, get_pending_payments
from logger_setup import logger

from handlers.start import start_command
from handlers.payment import handle_receipt
from handlers.admin import (
    set_price_command,
    check_guest_list_command,
    add_admin_command,
    remove_admin_command,
    add_guest_command,
    remove_guest_command,
    help_command,
)


async def notify_pending_payments(app: Application) -> None:
    """
    Проверяет, остались ли с прошлого запуска оплаты в статусе 'pending'
    (чек приняли, но что-то пошло не так до подтверждённой выдачи кодов —
    например, бот упал или сервер перезагрузился посреди обработки).

    Такие случаи НЕ обрабатываются автоматически — слишком рискованно
    повторно генерировать коды, не понимая, на каком именно шаге всё
    остановилось (коды могли быть выданы частично). Вместо этого
    супер-админу отправляется список для ручной проверки командой
    /check_guest_list и, если нужно, /add_guest.
    """
    pending = get_pending_payments()
    if not pending:
        logger.info("Зависших оплат не найдено")
        return

    logger.warning(f"Найдено {len(pending)} зависших оплат со статусом 'pending'")

    lines = [
        f"Квитанция {p['receipt_number']}, сумма {p['amount']}₽, от user_id={p['sender_user_id']}"
        for p in pending
    ]
    text = (
        f"⚠️ После перезапуска бота найдено {len(pending)} оплат(ы) в статусе 'pending'.\n"
        f"Это значит, что чек был принят, но коды могли быть выданы не полностью.\n"
        f"Проверьте вручную и при необходимости добавьте гостей командой /add_guest:\n\n"
        + "\n".join(lines)
    )

    try:
        await app.bot.send_message(chat_id=SUPER_ADMIN_ID, text=text)
    except Exception as e:
        # Если, например, супер-админ ещё ни разу не писал боту — Telegram не даст
        # отправить ему сообщение первым. В этом случае просто пишем в лог.
        logger.error(f"Не удалось отправить уведомление о зависших оплатах супер-админу: {e}")


async def post_init(app: Application) -> None:
    """Вызывается автоматически один раз сразу после запуска бота."""
    await notify_pending_payments(app)


def main() -> None:
    init_db()
    logger.info("База данных инициализирована")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # --- Команды, доступные всем пользователям ---
    app.add_handler(CommandHandler("start", start_command))

    # --- Команды только для администраторов ---
    app.add_handler(CommandHandler("set_price", set_price_command))
    app.add_handler(CommandHandler("check_guest_list", check_guest_list_command))
    app.add_handler(CommandHandler("add_admin", add_admin_command))
    app.add_handler(CommandHandler("remove_admin", remove_admin_command))
    app.add_handler(CommandHandler("add_guest", add_guest_command))
    app.add_handler(CommandHandler("remove_guest", remove_guest_command))
    app.add_handler(CommandHandler("help", help_command))

    # --- Кнопки клавиатуры админа (они просто отправляют обычный текст) ---
    app.add_handler(MessageHandler(filters.Text(["📋 Список оплативших"]), check_guest_list_command))
    app.add_handler(MessageHandler(filters.Text(["ℹ️ Список команд"]), help_command))

    # --- Приём файлов (чеков) от кого угодно ---
    app.add_handler(MessageHandler(filters.Document.PDF, handle_receipt))

    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
