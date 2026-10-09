"""
Точка входа в приложение.

Что происходит при запуске:
  1. Инициализируем базу данных (создаём таблицы, если их нет)
  2. Проверяем, не остались ли с прошлого запуска "зависшие" оплаты (статус 'pending')
  3. Регистрируем все обработчики
  4. Запускаем бота в режиме polling

Запуск: python main.py
"""

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import BOT_TOKEN, SUPER_ADMIN_ID
from database import init_db, get_pending_payments
from logger_setup import logger

from handlers.start import start_command
from handlers.buyer import handle_receipt
from handlers.router import handle_text
from handlers.admin import add_admin_command, remove_admin_command, help_command, myid_command


async def notify_pending_payments(app: Application) -> None:
    """
    Проверяет, остались ли с прошлого запуска оплаты в статусе 'pending'
    (чек приняли, но что-то пошло не так до подтверждённой выдачи кода).
    Автоматически ничего не досоздаётся — слишком рискованно. Вместо этого
    супер-админу отправляется список для ручной проверки.
    """
    pending = get_pending_payments()
    if not pending:
        logger.info("Зависших оплат не найдено")
        return

    logger.warning(f"Найдено {len(pending)} зависших оплат со статусом 'pending'")

    lines = [
        f"📋 Квитанция {p['receipt_number']}, сумма {p['amount']}₽, от user_id={p['sender_user_id']}"
        for p in pending
    ]
    text = (
        f"⚠️ После перезапуска бота найдено {len(pending)} оплат(ы) в статусе 'pending'.\n"
        f"Это значит, что чек был принят, но код мог быть выдан не полностью.\n"
        f"Проверьте вручную и при необходимости добавьте гостя кнопкой «Добавить гостя»:\n\n"
        + "\n".join(lines)
    )

    try:
        await app.bot.send_message(chat_id=SUPER_ADMIN_ID, text=text)
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление о зависших оплатах супер-админу: {e}")


async def post_init(app: Application) -> None:
    await notify_pending_payments(app)


def main() -> None:
    init_db()
    logger.info("База данных инициализирована")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # --- Команды ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    # Назначение/снятие админа обязательно идёт через reply на пересланное сообщение,
    # поэтому это остаётся командой, а не кнопкой
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("add_admin", add_admin_command))
    app.add_handler(CommandHandler("remove_admin", remove_admin_command))

    # --- Файлы (чеки) ---
    app.add_handler(MessageHandler(filters.Document.PDF, handle_receipt))

    # --- Весь остальной текст (кнопки, ответы в диалогах, коды на входе) ---
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запускается...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
