#!/bin/bash
# ASTRA BOT — Cron Jobs Setup
# ==========================================

echo "Установка cron jobs для ASTRA BOT..."
echo ""

# Директория скриптов
SCRIPT_DIR="/app/scripts"
REPORT_DIR="/app/reports"

# Создаём директорию для отчётов
mkdir -p "$REPORT_DIR"

# Бэкап базы данных каждый день в 2:00
echo "0 2 * * * cd $SCRIPT_DIR && ./backup.sh >> /app/logs/backup.log 2>&1" > /etc/cron.d/astra_backup
echo "✅ Бэкап базы данных: каждый день в 2:00"

# Ежедневный отчёт в 9:00
echo "0 9 * * * cd $SCRIPT_DIR && python daily_report.py >> /app/logs/report.log 2>&1" >> /etc/cron.d/astra_backup
echo "✅ Ежедневный отчёт: каждый день в 9:00"

# Проверка здоровья каждый час
echo "0 * * * * cd $SCRIPT_DIR && python -c \"from preflight import check_health; check_health()\" >> /app/logs/health.log 2>&1" >> /etc/cron.d/astra_backup
echo "✅ Проверка здоровья: каждый час"

# Перезапуск при падении (через systemd)
echo ""
echo "Для автоматического перезапуска используйте systemd:"
echo ""
echo "Создайте файл /etc/systemd/system/astra-bot.service:"
echo "
[Unit]
Description=ASTRA BOT — Autonomous Crypto Trading
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=botuser
WorkingDirectory=/app
Environment=\"PATH=/usr/local/bin:/usr/bin:/bin\"
EnvironmentFile=/app/.env
ExecStart=/app/scripts/start.sh
Restart=on-failure
RestartSec=60
StandardOutput=append:/app/logs/astra_bot.log
StandardError=append:/app/logs/astra_bot_error.log

[Install]
WantedBy=multi-user.target
"

echo ""
echo "Активация:"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable astra-bot"
echo "  sudo systemctl start astra-bot"
echo ""
echo "Мониторинг:"
echo "  sudo systemctl status astra-bot"
echo "  sudo journalctl -u astra-bot -f"
