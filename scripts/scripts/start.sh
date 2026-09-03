#!/bin/bash
# ASTRA BOT — Startup Script
# ==========================================

set -e

echo "=============================================="
echo "  ASTRA BOT — Starting"
echo "=============================================="

# Проверка переменных окружения
check_env() {
    local var_name=$1
    local var_value=${!var_name}
    
    if [ -z "$var_value" ] || [ "$var_value" = "YOUR_"* ]; then
        echo "⚠️  WARNING: $var_name не настроен!"
        if [ "$var_name" = "BINGX_API_KEY" ] || [ "$var_name" = "BINGX_API_SECRET" ]; then
            echo "   Ключи BingX опциональны: рыночные данные публичны,"
            echo "   ключи нужны только для баланса спот-счёта."
        fi
    fi
}

echo ""
echo "Проверка конфигурации..."
check_env "BINGX_API_KEY"
check_env "TELEGRAM_BOT_TOKEN"

# Инициализация базы данных
echo ""
echo "Инициализация базы данных..."
if [ -f "/app/init.sql" ]; then
    echo "  POSTGRES_HOST=$DB_HOST"
    echo "  POSTGRES_DB=$DB_NAME"
    # Здесь можно добавить автоматическую инициализацию
fi

# Проверка связи с биржей
echo ""
echo "Проверка связи с BingX..."
python -c "
import asyncio
import sys
sys.path.insert(0, '/app')
from astra_bot.adapters.bingx import BingXClient

async def test():
    config = {
        'api_key': '${BINGX_API_KEY}',
        'api_secret': '${BINGX_API_SECRET}',
        'enabled': True,
    }
    client = BingXClient(config)
    try:
        await client.initialize()
        result = await client.test_connection()
        if result:
            print('  ✅ BingX соединение успешно')
        else:
            print('  ⚠️  BingX соединение не удалось')
    except Exception as e:
        print(f'  ❌ BingX ошибка: {e}')
    finally:
        await client.close()

asyncio.run(test())
" 2>&1 || echo "  ⚠️  Не удалось проверить BingX (это нормально для продакшена)"

# Запуск приложения
echo ""
echo "Запуск ASTRA BOT..."
echo ""

exec python -m astra_bot.main \
    --config /app/config/settings.yaml \
    --env $ENVIRONMENT
