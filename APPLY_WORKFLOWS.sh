#!/bin/bash
# Скрипт для ручной установки workflow файлов (обходит ограничение GitHub App без workflows permission)
# Запусти локально: ./APPLY_WORKFLOWS.sh && git push

set -e
echo "=== Копирую workflows из backup ==="
cp -v workflows_backup/bot.yml .github/workflows/bot.yml
cp -v workflows_backup/morning-report.yml .github/workflows/morning-report.yml
cp -v workflows_backup/daily-retrain.yml .github/workflows/daily-retrain.yml
cp -v workflows_backup/weekly-adapt.yml .github/workflows/weekly-adapt.yml

echo "=== Проверяю ==="
ls -lh .github/workflows/*.yml

echo "=== Коммичу ==="
git add .github/workflows/bot.yml .github/workflows/morning-report.yml .github/workflows/daily-retrain.yml .github/workflows/weekly-adapt.yml
git commit -m "ci: workflows caching, rebase, force-with-lease, repository_dispatch, daily-retrain, weekly-adapt

- Block 1.1: pip cache
- Block 2.2: pull --rebase + force-with-lease
- Block 3.2: repository_dispatch for cron-job.org/UptimeRobot
- Block 1.5: logs/errors.log
- Block 2.1: data/* persistence
- daily-retrain 02:00 UTC, weekly-adapt Sun 03:00 UTC
" || echo "nothing to commit or already committed"

echo "=== Готово к push ==="
echo "Теперь запусти:"
echo "  git push origin master"
echo "или если ты на ветке arena:"
echo "  git push origin arena/01a07378-astra-bot"
echo ""
echo "После этого сделай PR arena -> master в GitHub UI"
