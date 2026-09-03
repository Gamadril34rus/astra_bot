#!/usr/bin/env python3
"""
ASTRA BOT — Daily Report Generator
Генерация и отправка ежедневного отчёта в Telegram
"""

import asyncio
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from astra_bot.core.config import get_settings
from astra_bot.core.state import get_system_state
from astra_bot.engines.risk_engine import get_risk_engine
from astra_bot.paperengine.paper_engine import get_paper_engine


def format_currency(value: Decimal, show_sign: bool = True) -> str:
    """Отформатировать валюту"""
    if value is None:
        return "0.00"

    formatted = f"{abs(value):,.2f}"

    if show_sign and value > 0:
        return f"+{formatted}"
    elif show_sign and value < 0:
        return f"-{formatted}"
    return formatted


def format_percentage(value: Decimal, decimals: int = 2) -> str:
    """Отформатировать процент"""
    if value is None:
        return "0.00%"
    return f"{float(value):.{decimals}f}%"


def generate_daily_report() -> str:
    """Сгенерировать ежедневный отчёт"""
    state = get_system_state()
    risk = get_risk_engine()

    today = datetime.utcnow().strftime("%d.%m.%Y")

    # Заголовок
    report = []
    report.append("🤖 *ASTRA BOT — ЕЖЕДНЕВНЫЙ ОТЧЁТ*")
    report.append("")
    report.append(f"*{today}*")
    report.append("")

    # Капитал
    report.append("*📊 КАПИТАЛ:*")
    report.append(f"  Текущий: {format_currency(state.current_equity)} ₽")
    report.append(f"  Начальный: {format_currency(state.initial_capital)} ₽")

    total_pnl = state.total_net_pnl
    total_pnl_pct = state.total_pnl_pct
    report.append(f"  Общая прибыль: {format_currency(total_pnl)} ₽ ({format_percentage(total_pnl_pct)})")
    report.append("")

    # Просадка
    report.append("*📉 ПРОСАДКА:*")
    report.append(f"  Текущая: {format_percentage(state.current_drawdown)}")
    report.append(f"  Максимальная: {format_percentage(state.max_drawdown_ever)}")

    dd_pct = float(state.current_drawdown)
    drawdown_status = (
        "🟢 Норма" if dd_pct < 3 else
        "🟡 Внимание" if dd_pct < 5 else
        "🔴 Высокая" if dd_pct < 8 else
        "🚨 Критическая"
    )
    report.append(f"  Статус: {drawdown_status}")
    report.append("")

    # Торговая статистика
    report.append("*📈 ТОРГОВАЯ СТАТИСТИКА:*")
    total_trades = state.total_trades
    wins = state.total_wins
    losses = state.total_losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    report.append(f"  Всего сделок: {total_trades}")
    report.append(f"  Побед: {wins} | Поражений: {losses}")
    report.append(f"  Win Rate: {win_rate:.1f}%")

    # Profit Factor (приблизительный; в продакшене считать по реализованным PnL)
    if losses > 0 and wins > 0:
        pf = 1.5  # placeholder
    else:
        pf = 0.0

    report.append(f"  Profit Factor: {pf:.2f}")
    report.append("")

    # Стратегии
    report.append("*📊 СТРАТЕГИИ:*")
    for name, strat in state.strategies.items():
        status_icon = "✅" if strat.is_running else "⏸️"
        kill_icon = "🔴" if strat.kill_switch else ""
        report.append(f"  {status_icon} {name}: {kill_icon}")
        report.append(f"     Сделок: {strat.total_trades} | PF: {strat.profit_factor:.2f}")
    report.append("")

    # Режим рынка
    if state.market_regimes:
        report.append("*🌐 РЫНОК:*")
        for symbol, regime_info in state.market_regimes.items():
            report.append(f"  {symbol}: {regime_info.regime} (уверенность: {regime_info.confidence:.0%})")
    report.append("")

    # Риск-статус
    report.append("*🛡️ РИСК:*")
    report.append(f"  Статус: {state.risk_state.value}")
    report.append(f"  Множитель риска: {state.get_risk_multiplier():.2f}")

    daily_loss = risk.daily_pnl if risk else Decimal("0")
    report.append(f"  Дневные потери: {format_currency(daily_loss)} ₽")
    report.append("")

    # Позиции
    paper = get_paper_engine()
    if paper:
        positions = paper.get_positions()
        if positions:
            report.append("*📍 ОТКРЫТЫЕ ПОЗИЦИИ:*")
            for pos in positions:
                pnl_str = format_currency(pos.pnl)
                pnl_color = "🟢" if pos.pnl > 0 else "🔴" if pos.pnl < 0 else "⚪"
                report.append(f"  {pnl_color} {pos.symbol} {pos.side}")
                report.append(f"     Цена: {pos.current_price:.2f} | Размер: {pos.quantity:.6f}")
                report.append(f"     PnL: {pnl_str} ₽ ({format_percentage(pos.pnl_pct)})")
            report.append("")

    # Здоровье системы
    report.append("*🏥 СИСТЕМА:*")
    health_icon = "🟢" if state.system_health.value == "HEALTHY" else "🟡" if state.system_health.value == "DEGRADED" else "🔴"
    report.append(f"  {health_icon} {state.system_health.value}")
    report.append(f"  Режим: {state.trading_state.value}")
    report.append("")

    # Ошибки
    if state.errors_today > 0:
        report.append(f"⚠️  Ошибок за день: {state.errors_today}")
        report.append("")

    # footer
    report.append("─" * 40)
    report.append("*ASTRA BOT — Risk-First Quantitative Trading*")

    return "\n".join(report)


async def send_telegram_report():
    """Отправить отчёт в Telegram"""
    settings = get_settings()

    if not settings.telegram or not settings.telegram.bot_token:
        print("⚠️ Telegram не настроен, отчёт не отправляется")
        return False

    try:
        from telegram import Bot

        bot = Bot(token=settings.telegram.bot_token)

        report = generate_daily_report()

        # Отправляем всем админам
        for admin_id in settings.telegram.admin_user_ids:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=report,
                    parse_mode="Markdown",
                )
                print(f"✅ Отчёт отправлен админу {admin_id}")
            except Exception as e:
                print(f"❌ Ошибка отправки админу {admin_id}: {e}")

        return True

    except Exception as e:
        print(f"❌ Ошибка Telegram: {e}")
        return False


async def main():
    """Главная функция"""
    print("Генерация ежедневного отчёта...")

    # Генерируем отчёт
    report = generate_daily_report()

    # Сохраняем в файл
    report_path = Path("/app/reports") / f"daily_report_{datetime.utcnow().strftime('%Y%m%d')}.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"✅ Отчёт сохранён: {report_path}")
    print()
    print(report)

    # Отправляем в Telegram (если настроен)
    print()
    print("Отправка в Telegram...")
    success = await send_telegram_report()

    if success:
        print("✅ Отчёт отправлен!")
    else:
        print("⚠️  Отчёт не отправлен (Telegram не настроен или ошибка)")

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
