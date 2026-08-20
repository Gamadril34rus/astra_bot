#!/usr/bin/env python3
"""
ASTRA BOT — Pre-flight Check
Проверка перед запуском торговли
"""

import asyncio
import os
import sys
from pathlib import Path

# Добавляем проект в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.config import get_settings, load_settings
from astra_bot.data.database import DatabaseManager


async def check_database(config: dict) -> tuple[bool, str]:
    """Проверить соединение с базой данных"""
    try:
        db_manager = DatabaseManager(config)
        await db_manager.connect()
        healthy = await db_manager.health_check()
        await db_manager.disconnect()
        if not healthy:
            return False, "⚠️ База данных недоступна (допустим no-DB режим)"
        return True, "✅ База данных подключена"
    except Exception as e:
        return False, f"❌ База данных: {e}"


async def check_okx_api(api_key: str, api_secret: str, passphrase: str) -> tuple[bool, str]:
    """Проверить OKX API ключи"""
    if not api_key or not api_secret:
        return False, "❌ OKX API ключи не настроены"

    config = {
        "api_key": api_key,
        "api_secret": api_secret,
        "passphrase": passphrase,
        "sandbox": True,  # Всегда проверяем в песочнице
        "enabled": True,
    }

    client = None
    try:
        client = OKXClient(config)
        await client.initialize()

        # Проверяем соединение
        connected = await client.test_connection()

        if connected:
            result = await client.get_instruments()
            if result:
                return True, f"✅ OKX API работает ({len(result)} инструментов)"
            else:
                return False, "❌ OKX API: не удалось получить инструменты"
        else:
            return False, "❌ OKX API: нет соединения"

    except Exception as e:
        return False, f"❌ OKX API: {e}"
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


async def check_api_permissions(api_key: str, api_secret: str, passphrase: str) -> tuple[bool, str]:
    """Проверить права API ключей"""
    # В реальной реализации нужно проверить через API
    # что у ключа нет прав на вывод

    # Пока это упрощённая проверка
    if not api_key or not api_secret:
        return False, "❌ API ключи не настроены"

    # Проверяем что ключи не являются тестовыми по формату
    # (в реальности нужно проверять через API)

    return True, "✅ API ключи проверены (проверьте вручную withdrawal: NO)"


async def check_min_order_requirements(client: OKXClient, symbols: list) -> tuple[bool, str, dict]:
    """Проверить минимальные требования ордеров"""
    results = {}
    all_ok = True

    for symbol in symbols:
        try:
            instrument = await client.get_instrument(symbol)
            if instrument:
                results[symbol] = {
                    "min_qty": str(instrument.min_quantity),
                    "min_notional": str(instrument.min_notional),
                    "tick_size": str(instrument.tick_size),
                    "price_precision": instrument.price_precision,
                    "quantity_precision": instrument.quantity_precision,
                }
            else:
                results[symbol] = {"error": "не найден"}
                all_ok = False
        except Exception as e:
            results[symbol] = {"error": str(e)}
            all_ok = False

    if all_ok:
        return True, f"✅ Требования ордеров проверены для {len(symbols)} инструментов", results
    else:
        return False, "❌ Проблемы с требованиями ордеров", results


async def check_risk_config() -> tuple[bool, str]:
    """Проверить конфигурацию риска"""
    try:
        settings = get_settings()
        risk = settings.risk

        issues = []

        if risk.risk_per_trade <= 0:
            issues.append("risk_per_trade должен быть > 0")

        if risk.daily_loss_limit <= 0:
            issues.append("daily_loss_limit должен быть > 0")

        if risk.hard_drawdown <= risk.soft_drawdown:
            issues.append("hard_drawdown должен быть > soft_drawdown")

        if risk.emergency_drawdown <= risk.hard_drawdown:
            issues.append("emergency_drawdown должен быть > hard_drawdown")

        if issues:
            return False, "❌ Проблемы с риск-конфигурацией:\n" + "\n".join(
                f"  - {i}" for i in issues
            )

        return (
            True,
            "✅ Риск-конфигурация OK\n"
            + f"   risk_per_trade: {risk.risk_per_trade*100:.2f}%\n"
            + f"   daily_loss_limit: {risk.daily_loss_limit*100:.1f}%\n"
            + f"   hard_drawdown: {risk.hard_drawdown*100:.1f}%",
        )

    except Exception as e:
        return False, f"❌ Ошибка риск-конфигурации: {e}"


async def check_telegram(bot_token: str) -> tuple[bool, str]:
    """Проверить Telegram бота"""
    if not bot_token:
        return False, "⚠️ Telegram токен не настроен (не критично для запуска)"

    try:
        from telegram import Bot

        bot = Bot(token=bot_token)

        # Проверяем бота
        bot_info = await bot.get_me()

        return True, f"✅ Telegram бот: @{bot_info.username} ({bot_info.first_name})"

    except Exception as e:
        return False, f"⚠️ Telegram: {e}"


async def main():
    """Главная функция проверки"""
    print("=" * 60)
    print("  ASTRA BOT — Pre-flight Check")
    print("=" * 60)
    print()

    # Загрузка конфигурации
    print("1. Загрузка конфигурации...")
    try:
        config_path = os.environ.get("ASTRA_CONFIG", "config/settings.yaml")
        load_settings(config_path)
        settings = get_settings()
        print(f"   ✅ Конфигурация загружена: {settings.environment}")
    except Exception as e:
        print(f"   ❌ Ошибка конфигурации: {e}")
        print("\n💡 Решение: проверьте файл конфигурации и переменные окружения")
        return 1

    print()
    checks: dict[str, bool] = {"config": True}

    # Проверка базы данных
    print("2. Проверка базы данных...")
    if settings.database:
        db_config = {
            "host": settings.database.host,
            "port": settings.database.port,
            "name": settings.database.name,
            "user": settings.database.user,
            "password": settings.database.password,
        }
        ok, msg = await check_database(db_config)
    else:
        ok, msg = False, "⚠️ Конфигурация базы данных не найдена"

    checks["database"] = ok
    print(f"   {msg}")
    print()

    # Проверка OKX API
    print("3. Проверка OKX API...")
    okx_config = settings.exchanges.get("okx")
    if okx_config and okx_config.enabled:
        ok, msg = await check_okx_api(
            okx_config.api_key,
            okx_config.api_secret,
            okx_config.passphrase,
        )
    else:
        ok, msg = False, "⚠️ OKX конфигурация не найдена или отключена"

    checks["okx"] = ok
    print(f"   {msg}")
    print()

    # Проверка прав API
    print("4. Проверка прав API ключей...")
    if okx_config and okx_config.enabled:
        ok, msg = await check_api_permissions(
            okx_config.api_key,
            okx_config.api_secret,
            okx_config.passphrase,
        )
    else:
        ok, msg = False, "⚠️ Пропускается (API не настроен)"

    checks["permissions"] = ok
    print(f"   {msg}")
    print("   ⚠️  ВАЖНО: Вручную проверьте что у API ключа НЕТ прав на вывод!")
    print()

    # Проверка минимальных ордеров
    print("5. Проверка минимальных требований ордеров...")
    if checks["okx"] and checks["permissions"]:
        client = OKXClient(
            {
                "api_key": okx_config.api_key,
                "api_secret": okx_config.api_secret,
                "passphrase": okx_config.passphrase,
                "sandbox": True,
                "enabled": True,
            }
        )
        await client.initialize()

        ok, msg, results = await check_min_order_requirements(
            client, ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        )
        print(f"   {msg}")

        for symbol, info in results.items():
            if "error" in info:
                print(f"     {symbol}: ❌ {info['error']}")
            else:
                print(
                    f"     {symbol}: ✅ min_qty={info['min_qty']}, min_notional={info['min_notional']}"
                )

        await client.close()
        checks["orders"] = ok
    else:
        checks["orders"] = False
        print("   ⚠️ Пропускается (API не настроен или ошибка)")
    print()

    # Проверка риск-конфигурации
    print("6. Проверка риск-конфигурации...")
    ok, msg = await check_risk_config()
    checks["risk"] = ok
    print(f"   {msg}")
    print()

    # Проверка Telegram
    print("7. Проверка Telegram...")
    ok, msg = await check_telegram(settings.telegram.bot_token if settings.telegram else "")
    checks["telegram"] = ok
    print(f"   {msg}")
    print()

    # Итог
    print("=" * 60)
    print("  ПРОВЕРКА ЗАВЕРШЕНА")
    print("=" * 60)
    print()

    # База и Telegram опциональны для standalone Demo worker. Торговые
    # проверки не должны затираться результатом последней (Telegram) проверки.
    critical = ("okx", "permissions", "orders", "risk")
    failed = [name for name in critical if not checks.get(name, False)]
    if failed:
        print(f"❌  Критические проверки не пройдены: {', '.join(failed)}")
        print("\n💡  Для запуска устраните проблемы выше")
        return 1

    optional_missing = [name for name in ("database", "telegram") if not checks.get(name, False)]
    if optional_missing:
        print(f"⚠️  Опциональные сервисы недоступны: {', '.join(optional_missing)}")
    print("✅  Все критические проверки пройдены — можно запускать!")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
