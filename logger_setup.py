"""
Настройка логирования.

Бот пишет логи в двух местах:
  - в консоль (чтобы видеть, что происходит, пока разрабатываешь в PyCharm)
  - в файл logs/bot.log с ежедневной ротацией: каждую полночь создаётся
    новый файл, а старый переименовывается с датой в конце имени
    (например, bot.log.2026-09-29). Так логи не разрастаются в один
    бесконечный файл и их удобно искать по дате.
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from config import LOG_DIR


def setup_logger() -> logging.Logger:
    # Создаём папку для логов, если её ещё нет (например, при самом первом запуске)
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("basket_pay_bot")
    logger.setLevel(logging.INFO)

    # Формат одной строки лога: время | уровень | сообщение
    log_format = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    # --- Вывод в консоль ---
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)

    # --- Запись в файл с ежедневной ротацией ---
    log_file_path = os.path.join(LOG_DIR, "bot.log")
    file_handler = TimedRotatingFileHandler(
        log_file_path,
        when="midnight",   # менять файл каждую полночь
        interval=1,        # раз в 1 день
        backupCount=90,    # хранить логи максимум за последние 90 дней, дальше старые удаляются
        encoding="utf-8",
    )
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)

    return logger


# Единый объект логгера. Импортируется во всех остальных файлах вот так:
#     from logger_setup import logger
#     logger.info("что-то произошло")
logger = setup_logger()
