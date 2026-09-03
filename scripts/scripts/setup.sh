#!/usr/bin/env bash
# ASTRA BOT — быстрая настройка на Linux/macOS.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Python venv"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
pip install python-dotenv >/dev/null

echo
echo "==> .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Создан .env из .env.example. Откройте его и впишите BINGX_API_KEY/BINGX_API_SECRET."
else
    echo ".env уже существует."
fi

echo
echo "==> Проверка синтаксиса и тесты"
python -m pytest -q

echo
echo "==> Готово. Дальше:"
echo "  1) nano .env                # впишите BingX ключи (опционально)"
echo "  2) python scripts/test_bingx.py"
echo "  3) python scripts/train_multi_timeframe.py --days 1095"
echo "  4) python scripts/run_paper.py"
