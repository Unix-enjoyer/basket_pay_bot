"""
Обработчик команды /start.
Показывает приветствие и кнопку покупки билета.
Если пишет администратор — показывает расширенную клавиатуру с админ-кнопками.
"""

from telegram import Update
from telegram.ext import ContextTypes

from database import is_admin
from handlers.keyboards import BUYER_KEYBOARD, ADMIN_KEYBOARD, ADMIN_HELP_TEXT
from logger_setup import logger

WELCOME_TEXT = (
    "Привет! Здесь можно купить билет на турнир.\n\n"
    "Билет можно купить только на себя — если с тобой будут дети до 10 лет, "
    "они проходят бесплатно, просто укажешь их число, когда бот спросит.\n\n"
    "Нажми кнопку «🎟 Купить билет себе» ниже, чтобы начать."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(f"/start от user_id={user.id} (@{user.username})")

    if is_admin(user.id):
        await update.message.reply_text(WELCOME_TEXT, reply_markup=ADMIN_KEYBOARD)
        await update.message.reply_text("Вы администратор.\n\n" + ADMIN_HELP_TEXT)
    else:
        await update.message.reply_text(WELCOME_TEXT, reply_markup=BUYER_KEYBOARD)
