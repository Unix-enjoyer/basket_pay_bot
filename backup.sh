#!/bin/bash
# Ежедневный бэкап базы бота. Запускается по расписанию через cron (см. строку ниже).
#
# Как подключить (один раз, на сервере):
#   1) chmod +x backup.sh
#   2) crontab -e   и добавить строку (каждый день в 4:00 по времени сервера):
#        0 4 * * * /ПОЛНЫЙ/ПУТЬ/К/ПРОЕКТУ/backup.sh >> /ПОЛНЫЙ/ПУТЬ/К/ПРОЕКТУ/logs/backup.log 2>&1
#      (полный путь покажет команда pwd в папке проекта)
#
# Копия делается встроенным механизмом SQLite, поэтому безопасна, даже пока бот работает.
# Старые копии (старше 14 дней) удаляются автоматически.

# Переходим в папку, где лежит сам скрипт (корень проекта), чтобы пути работали из cron
cd "$(dirname "$0")" || exit 1

DB_FILE="data/bot.db"
BACKUP_DIR="backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ОШИБКА: файл $DB_FILE не найден"
    exit 1
fi

BACKUP_FILE="$BACKUP_DIR/bot_$(date +%Y-%m-%d_%H-%M).db"

python3 - "$DB_FILE" "$BACKUP_FILE" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY

if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') Бэкап создан: $BACKUP_FILE"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ОШИБКА при создании бэкапа"
    exit 1
fi

# Удаляем копии старше KEEP_DAYS дней
find "$BACKUP_DIR" -name "bot_*.db" -mtime +"$KEEP_DAYS" -delete
