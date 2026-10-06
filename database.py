"""
Работа с базой данных SQLite.

ВАЖНО: это единственный файл в проекте, который напрямую обращается к базе
данных. Все остальные файлы вызывают функции отсюда и никогда не пишут SQL сами.

Таблицы:
  payments            — оплаты по чекам (один чек = один билет)
  guests              — выданные коды входа (= "билеты"), включая добавленных вручную
  admins              — список администраторов
  price_history       — история изменений цены
  pending_purchases   — незавершённые покупки обычных пользователей (ждём чек / телефон)
  admin_pending_actions — незавершённые диалоги с админами (ждём новую цену / тег гостя / код для удаления и т.д.)

Два "pending"-состояния специально хранятся в базе, а не в памяти процесса —
чтобы при падении или перезапуске бота никто не "потерялся" на середине диалога.
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
    использования. Если внутри блока ничего не упало — изменения сохраняются
    (commit), если упало — откатываются (rollback), чтобы не оставить
    "половину" операции в базе.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    """Создаёт все таблицы, если их ещё нет. Вызывается один раз при старте бота."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_number  TEXT UNIQUE NOT NULL,
                amount          INTEGER NOT NULL,
                sender_user_id  INTEGER NOT NULL,
                sender_tag      TEXT,
                price_used      INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                code            TEXT PRIMARY KEY,
                tag_or_phone    TEXT NOT NULL,
                user_id         INTEGER,
                payment_id      INTEGER,
                added_by_admin  INTEGER,
                comment         TEXT,
                checked_in_at   TEXT,
                checked_in_by   INTEGER,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (payment_id) REFERENCES payments (id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id     INTEGER PRIMARY KEY,
                tag         TEXT,
                added_by    INTEGER,
                added_at    TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                price       INTEGER NOT NULL,
                changed_by  INTEGER NOT NULL,
                changed_at  TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_purchases (
                user_id         INTEGER PRIMARY KEY,
                stage           TEXT NOT NULL,
                price_locked    INTEGER,
                payment_id      INTEGER,
                created_at      TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_pending_actions (
                admin_id    INTEGER PRIMARY KEY,
                action      TEXT NOT NULL,
                temp_tag    TEXT,
                created_at  TEXT NOT NULL
            )
        """)

    if get_current_price() is None:
        set_price(DEFAULT_PRICE, changed_by=SUPER_ADMIN_ID)
        logger.info(f"Установлена начальная цена билета по умолчанию: {DEFAULT_PRICE}")

    if not is_admin(SUPER_ADMIN_ID):
        add_admin(SUPER_ADMIN_ID, tag=None, added_by=SUPER_ADMIN_ID)
        logger.info(f"Супер-админ {SUPER_ADMIN_ID} добавлен в таблицу admins")


# ==================== ЦЕНА БИЛЕТА ====================

def get_current_price() -> Optional[int]:
    with get_connection() as conn:
        row = conn.execute("SELECT price FROM price_history ORDER BY id DESC LIMIT 1").fetchone()
        return row["price"] if row else None


def set_price(price: int, changed_by: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (price, changed_by, changed_at) VALUES (?, ?, ?)",
            (price, changed_by, datetime.now().isoformat()),
        )
    logger.info(f"Цена билета изменена на {price}₽ администратором {changed_by}")


# ==================== АДМИНЫ ====================

def is_admin(user_id: int) -> bool:
    with get_connection() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone() is not None


def add_admin(user_id: int, tag: Optional[str], added_by: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, tag, added_by, added_at) VALUES (?, ?, ?, ?)",
            (user_id, tag, added_by, datetime.now().isoformat()),
        )
    logger.info(f"Админ {user_id} (@{tag}) добавлен пользователем {added_by}")


def remove_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return False
    with get_connection() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    logger.info(f"Админ {user_id} удалён")
    return True


# ==================== ОПЛАТЫ ====================

def receipt_exists(receipt_number: str) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM payments WHERE receipt_number = ?", (receipt_number,)
        ).fetchone() is not None


def add_payment(
    receipt_number: str,
    amount: int,
    sender_user_id: int,
    sender_tag: Optional[str],
    price_used: int,
) -> Optional[int]:
    """Записывает оплату со статусом 'pending'. Возвращает id, либо None при дубле квитанции."""
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO payments
                   (receipt_number, amount, sender_user_id, sender_tag, price_used, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (receipt_number, amount, sender_user_id, sender_tag, price_used, datetime.now().isoformat()),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        logger.warning(f"Попытка повторной обработки квитанции {receipt_number}")
        return None


def mark_payment_sent(payment_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE payments SET status = 'sent' WHERE id = ?", (payment_id,))


def get_pending_payments() -> list:
    """Оплаты, застрявшие в статусе 'pending' — проверяется при каждом старте бота."""
    with get_connection() as conn:
        return conn.execute("SELECT * FROM payments WHERE status = 'pending'").fetchall()


# ==================== НЕЗАВЕРШЁННЫЕ ПОКУПКИ (обычные пользователи) ====================

def set_pending_purchase(
    user_id: int,
    stage: str,
    price_locked: Optional[int] = None,
    payment_id: Optional[int] = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO pending_purchases (user_id, stage, price_locked, payment_id, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   stage = excluded.stage,
                   price_locked = excluded.price_locked,
                   payment_id = excluded.payment_id""",
            (user_id, stage, price_locked, payment_id, datetime.now().isoformat()),
        )


def get_pending_purchase(user_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM pending_purchases WHERE user_id = ?", (user_id,)).fetchone()


def clear_pending_purchase(user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM pending_purchases WHERE user_id = ?", (user_id,))


# ==================== НЕЗАВЕРШЁННЫЕ ДЕЙСТВИЯ АДМИНОВ ====================

def set_admin_pending_action(admin_id: int, action: str, temp_tag: Optional[str] = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO admin_pending_actions (admin_id, action, temp_tag, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(admin_id) DO UPDATE SET
                   action = excluded.action,
                   temp_tag = excluded.temp_tag""",
            (admin_id, action, temp_tag, datetime.now().isoformat()),
        )


def get_admin_pending_action(admin_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM admin_pending_actions WHERE admin_id = ?", (admin_id,)
        ).fetchone()


def clear_admin_pending_action(admin_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM admin_pending_actions WHERE admin_id = ?", (admin_id,))


# ==================== ГОСТИ / КОДЫ ВХОДА ====================

def code_exists(code: str) -> bool:
    with get_connection() as conn:
        return conn.execute("SELECT 1 FROM guests WHERE code = ?", (code,)).fetchone() is not None


def add_guest(
    code: str,
    tag_or_phone: str,
    user_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    added_by_admin: Optional[int] = None,
    comment: Optional[str] = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO guests (code, tag_or_phone, user_id, payment_id, added_by_admin, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (code, tag_or_phone, user_id, payment_id, added_by_admin, comment, datetime.now().isoformat()),
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


def get_guests_with_user_id() -> list:
    """
    Гости, которым можно написать напрямую в Telegram (их user_id известен,
    потому что они сами оплачивали билет через бота).
    Гости, добавленные вручную админом по тегу/телефону, сюда не попадают —
    бот не знает их Telegram-аккаунт и физически не может им написать.
    """
    with get_connection() as conn:
        return conn.execute("SELECT * FROM guests WHERE user_id IS NOT NULL").fetchall()


# ==================== ВХОД НА ТУРНИР (чек-ин) ====================

def check_in_guest(code: str, admin_id: int) -> Optional[dict]:
    """
    Пытается отметить гостя как пришедшего.
    Возвращает:
      None                                   — код не найден
      {"already": True, ...}                 — уже был отмечен ранее
      {"already": False, "tag_or_phone": ..} — только что отмечен, успех
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM guests WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None

        if row["checked_in_at"]:
            return {
                "already": True,
                "tag_or_phone": row["tag_or_phone"],
                "checked_in_at": row["checked_in_at"],
            }

        conn.execute(
            "UPDATE guests SET checked_in_at = ?, checked_in_by = ? WHERE code = ?",
            (datetime.now().isoformat(), admin_id, code),
        )
        return {"already": False, "tag_or_phone": row["tag_or_phone"]}


def get_last_checkin_by_admin(admin_id: int):
    """Последний вход, отмеченный ИМЕННО этим админом — для кнопки «отменить последний вход»."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM guests
               WHERE checked_in_by = ? AND checked_in_at IS NOT NULL
               ORDER BY checked_in_at DESC LIMIT 1""",
            (admin_id,),
        ).fetchone()


def cancel_checkin(code: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE guests SET checked_in_at = NULL, checked_in_by = NULL WHERE code = ?", (code,)
        )
    logger.info(f"Отменена отметка о входе для кода {code}")


# ==================== СТАТИСТИКА ПО ПРИХОДУ ====================

def get_attendance_stats() -> dict:
    """
    total              — всего выдано билетов
    checked_in_count   — сколько уже отмечено на входе
    not_checked_in     — теги/телефоны тех, кто ещё не пришёл
    price_breakdown    — {цена: число оплативших по этой цене} (только по чекам)
    manual_count       — сколько гостей добавлено вручную админами (без чека)
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT g.tag_or_phone, g.checked_in_at, p.price_used
            FROM guests g
            LEFT JOIN payments p ON g.payment_id = p.id
        """).fetchall()

    total = len(rows)
    checked_in_count = sum(1 for r in rows if r["checked_in_at"])
    not_checked_in = [r["tag_or_phone"] for r in rows if not r["checked_in_at"]]

    price_breakdown: dict = {}
    manual_count = 0
    for r in rows:
        if r["price_used"] is not None:
            price_breakdown[r["price_used"]] = price_breakdown.get(r["price_used"], 0) + 1
        else:
            manual_count += 1

    return {
        "total": total,
        "checked_in_count": checked_in_count,
        "not_checked_in": not_checked_in,
        "price_breakdown": price_breakdown,
        "manual_count": manual_count,
    }
