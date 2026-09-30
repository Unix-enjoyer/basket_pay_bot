"""
Работа с базой данных SQLite.

ВАЖНО: это единственный файл в проекте, который напрямую обращается к базе
данных. Все остальные файлы (обработчики команд, разбор чеков и т.д.)
вызывают функции отсюда и никогда не пишут SQL-запросы сами. Так проще
найти и починить баг, если что-то в данных пойдёт не так.

Таблицы:
  payments       — все обработанные оплаты (по номеру квитанции)
  guests         — коды входа, выданные людям (код -> тег/телефон)
  admins         — список администраторов бота
  price_history  — история изменений цены билета (кто и когда менял)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from config import DB_PATH, DEFAULT_PRICE, SUPER_ADMIN_ID
from logger_setup import logger


@contextmanager
def get_connection():
    """
    Открывает соединение с базой данных и гарантированно закрывает его после
    использования — даже если внутри блока произошла ошибка.

    Использование:
        with get_connection() as conn:
            conn.execute("SELECT ...")

    Если внутри блока ничего не упало — изменения сохраняются (commit).
    Если случилась ошибка — все изменения этого блока отменяются (rollback),
    чтобы в базе не осталась "половина" операции.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # позволяет обращаться к колонкам по имени: row["amount"]
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    Создаёт все таблицы, если их ещё нет (при повторном запуске ничего не
    ломает и не затирает — CREATE TABLE IF NOT EXISTS просто ничего не
    сделает, если таблица уже есть).

    Вызывается один раз при старте бота, см. main.py.
    """
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_number  TEXT UNIQUE NOT NULL,   -- номер квитанции; UNIQUE не даёт записать дубль,
                                                         -- даже если два одинаковых чека прилетят одновременно
                amount          INTEGER NOT NULL,       -- сумма перевода из чека, в рублях
                sender_user_id  INTEGER NOT NULL,       -- Telegram user_id того, кто прислал чек боту
                sender_tag      TEXT,                   -- его @username на момент отправки чека
                comment_raw     TEXT,                   -- необработанный текст поля "Сообщение" из чека
                price_used      INTEGER NOT NULL,       -- цена билета, по которой считали сумму
                status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending' -> 'sent', либо 'failed'
                created_at      TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                code            TEXT PRIMARY KEY,       -- код входа, например ABC123
                tag_or_phone    TEXT NOT NULL,           -- @username или +7... того, за кого выдан код
                user_id         INTEGER,                 -- Telegram user_id, если гость сам писал боту (может быть NULL)
                payment_id      INTEGER,                 -- ссылка на оплату, по которой выдан код (NULL, если добавлен вручную админом)
                added_by_admin  INTEGER,                 -- user_id админа, если гость добавлен вручную командой /add_guest
                created_at      TEXT NOT NULL,
                FOREIGN KEY (payment_id) REFERENCES payments (id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id     INTEGER PRIMARY KEY,
                tag         TEXT,
                added_by    INTEGER,                     -- user_id того, кто назначил этого админа
                added_at    TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                price       INTEGER NOT NULL,
                changed_by  INTEGER NOT NULL,             -- user_id админа, который изменил цену
                changed_at  TEXT NOT NULL
            )
        """)

    # Если истории цен ещё нет — записываем цену по умолчанию из .env
    if get_current_price() is None:
        set_price(DEFAULT_PRICE, changed_by=SUPER_ADMIN_ID)
        logger.info(f"Установлена начальная цена билета по умолчанию: {DEFAULT_PRICE}")

    # Супер-админ должен всегда быть в таблице admins, даже после первого запуска
    if not is_admin(SUPER_ADMIN_ID):
        add_admin(SUPER_ADMIN_ID, tag=None, added_by=SUPER_ADMIN_ID)
        logger.info(f"Супер-админ {SUPER_ADMIN_ID} добавлен в таблицу admins")


# ==================== ЦЕНА БИЛЕТА ====================

def get_current_price() -> Optional[int]:
    """Возвращает текущую (последнюю по времени) цену билета, либо None, если истории ещё нет."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT price FROM price_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["price"] if row else None


def set_price(price: int, changed_by: int) -> None:
    """
    Добавляет новую запись в историю цен.
    Старые записи никогда не удаляются и не перезаписываются — это история изменений.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (price, changed_by, changed_at) VALUES (?, ?, ?)",
            (price, changed_by, datetime.now().isoformat()),
        )
    logger.info(f"Цена билета изменена на {price}₽ администратором {changed_by}")


def get_price_history(limit: int = 20) -> list:
    """Возвращает последние изменения цены, самые новые — первыми."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM price_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


# ==================== АДМИНЫ ====================

def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def add_admin(user_id: int, tag: Optional[str], added_by: int) -> None:
    with get_connection() as conn:
        # OR IGNORE — если такой user_id уже есть в admins, просто ничего не делаем,
        # вместо того чтобы упасть с ошибкой "уже существует"
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, tag, added_by, added_at) VALUES (?, ?, ?, ?)",
            (user_id, tag, added_by, datetime.now().isoformat()),
        )
    logger.info(f"Админ {user_id} (@{tag}) добавлен пользователем {added_by}")


def remove_admin(user_id: int) -> bool:
    """
    Удаляет админа. Возвращает False и ничего не делает, если пытаются
    удалить супер-админа — это запрещено на уровне кода, а не только "по договорённости".
    """
    if user_id == SUPER_ADMIN_ID:
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    logger.info(f"Админ {user_id} удалён")
    return True


def get_all_admins() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM admins").fetchall()


# ==================== ОПЛАТЫ ====================

def receipt_exists(receipt_number: str) -> bool:
    """
    Быстрая предварительная проверка на дубль номера квитанции.
    Это НЕ единственная защита от дублей — финальная и самая надёжная
    проверка происходит в add_payment() через UNIQUE-ограничение колонки,
    которое защищает и от гонки, если два одинаковых чека придут одновременно.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM payments WHERE receipt_number = ?", (receipt_number,)
        ).fetchone()
        return row is not None


def add_payment(
    receipt_number: str,
    amount: int,
    sender_user_id: int,
    sender_tag: Optional[str],
    comment_raw: str,
    price_used: int,
) -> Optional[int]:
    """
    Записывает оплату со статусом 'pending' (то есть "принята, но коды ещё не
    подтверждены как выданные"). Дальше payment.py вызовет mark_payment_sent()
    после того, как коды точно будут сохранены.

    Возвращает id новой записи, либо None, если такая квитанция уже есть
    в базе (сработала UNIQUE-защита).
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO payments
                   (receipt_number, amount, sender_user_id, sender_tag, comment_raw, price_used, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (receipt_number, amount, sender_user_id, sender_tag, comment_raw, price_used, datetime.now().isoformat()),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        # UNIQUE-ограничение на receipt_number сработало — это дубль
        logger.warning(f"Попытка повторной обработки квитанции {receipt_number}")
        return None


def mark_payment_sent(payment_id: int) -> None:
    """Помечает оплату как полностью обработанную (коды точно выданы)."""
    with get_connection() as conn:
        conn.execute("UPDATE payments SET status = 'sent' WHERE id = ?", (payment_id,))


def mark_payment_failed(payment_id: int) -> None:
    """Помечает оплату как проблемную — требует ручной проверки админом."""
    with get_connection() as conn:
        conn.execute("UPDATE payments SET status = 'failed' WHERE id = ?", (payment_id,))


def get_pending_payments() -> list:
    """
    Оплаты, застрявшие в статусе 'pending' — то есть чек приняли и записали,
    но нет подтверждения, что коды выданы до конца (например, бот упал
    посреди этого процесса). Проверяется при каждом старте бота, см. main.py.
    """
    with get_connection() as conn:
        return conn.execute("SELECT * FROM payments WHERE status = 'pending'").fetchall()


# ==================== ГОСТИ / КОДЫ ВХОДА ====================

def code_exists(code: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM guests WHERE code = ?", (code,)).fetchone()
        return row is not None


def add_guest(
    code: str,
    tag_or_phone: str,
    user_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    added_by_admin: Optional[int] = None,
) -> None:
    """
    Сохраняет выданный код.
    payment_id заполняется, если код выдан автоматически по чеку.
    added_by_admin заполняется, если код добавлен вручную командой /add_guest.
    """
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO guests (code, tag_or_phone, user_id, payment_id, added_by_admin, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code, tag_or_phone, user_id, payment_id, added_by_admin, datetime.now().isoformat()),
        )
    logger.info(f"Выдан код {code} для {tag_or_phone}")


def remove_guest(code: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM guests WHERE code = ?", (code,))
        removed = cursor.rowcount > 0
    if removed:
        logger.info(f"Гость с кодом {code} удалён")
    return removed


def get_all_guests() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM guests ORDER BY created_at").fetchall()


def get_guests_by_payment(payment_id: int) -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM guests WHERE payment_id = ?", (payment_id,)).fetchall()
