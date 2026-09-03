#!/bin/bash
# ASTRA BOT — Backup Script
# ==========================================

set -e

BACKUP_DIR="/backups"
DB_HOST="${DB_HOST:-localhost}"
DB_NAME="${DB_NAME:-astra_bot}"
DB_USER="${DB_USER:-astra}"
DB_PASSWORD="${DB_PASSWORD:-}"
RETENTION_DAYS=30

echo "=============================================="
echo "  ASTRA BOT — Backup"
echo "=============================================="
echo "Время: $(date)"
echo ""

# Создаём директорию бэкапов
mkdir -p "$BACKUP_DIR"

# Бэкап базы данных
if [ -n "$DB_PASSWORD" ]; then
    PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" \
        --format=custom \
        --compress=6 \
        --file="$BACKUP_DIR/db_$(date +%Y%m%d_%H%M%S).dump" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        echo "✅ Бэкап базы данных создан"
    else
        echo "❌ Ошибка бэкапа базы данных"
        exit 1
    fi
else
    echo "⚠️  Пароль базы данных не указан, пропускаем бэкап"
fi

# Бэкап конфигурации
if [ -d "/app/config" ]; then
    tar -czf "$BACKUP_DIR/config_$(date +%Y%m%d_%H%M%S).tar.gz" \
        -C /app config/ 2>/dev/null || true
    echo "✅ Бэкап конфигурации создан"
fi

# Очистка старых бэкапов
echo "Очистка бэкапов старше $RETENTION_DAYS дней..."
find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

# Показываем статус
echo ""
echo "Бэкапы в $BACKUP_DIR:"
ls -lh "$BACKUP_DIR" 2>/dev/null || echo "  Нет бэкапов"
echo ""
echo "✅ Бэкап завершён"
