"""Специализированные юнит-тесты для нового функционала Full Research Audit 2026."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from astra_bot.decision.strategy_registry import STRATEGY_REGISTRY, execution_strategies
from astra_bot.telegram.bot import AstraTelegramBot
from scripts.fetch_klines import main as fetch_main


def test_fetch_klines_cli_interface(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr("sys.argv", [
        "fetch_klines.py",
        "--symbols", "BTCUSDT,ETHUSDT",
        "--timeframes", "1h,4h",
        "--start", "2024-01-01",
        "--end", "2024-01-02",
        "--data-dir", str(data_dir),
    ])
    # Перехватываем спуск на сеть (fetch_* возвращает (csv_text, meta))
    sample_csv = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n1704067200000,1,2,0.5,1.5,100,1704070799999,1000,1,50,500,0\n"
    monkeypatch.setattr("scripts.fetch_klines.fetch_binance_vision", lambda sym, tf, s, e: (sample_csv, {"source": "binance_vision", "candles": 1}))
    code = fetch_main()
    assert code == 0
    assert (data_dir / "BTCUSDT_1h.csv").exists()
    assert (data_dir / "ETHUSDT_4h.csv").exists()


def test_no_auto_promotion_of_research_strategies():
    for _k, v in STRATEGY_REGISTRY.items():
        if v.tier != "champion":
            assert v.execution_blocked_reason is not None
            assert len(v.execution_blocked_reason) > 0

    exec_strats = execution_strategies()
    assert len(exec_strats) == 0


@pytest.mark.asyncio
async def test_telegram_audit_commands_handling(tmp_path, monkeypatch):
    bot = AstraTelegramBot(bot_token="123456:ABC-DEF1234ghIkl-zyx543210")
    bot.allowed_user_ids = {1001}

    mock_update = MagicMock()
    mock_update.effective_user.id = 1001
    mock_update.message.reply_text = AsyncMock()

    mock_context = MagicMock()

    # В отсутствие отчётов
    monkeypatch.setattr("pathlib.Path.exists", lambda p: False)

    await bot._cmd_audit_status(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_with(
        "🔍 Статус аудита: прогресс-файл не найден (`reports/research_2026/progress.json`).",
        parse_mode="Markdown",
        reply_markup=None,
    )

    await bot._cmd_audit_summary(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_with(
        "📄 Сводка аудита еще не сформирована (`reports/research_2026/aggregate_summary.md`).",
        parse_mode="Markdown",
        reply_markup=None,
    )

    await bot._cmd_strategies(mock_update, mock_context)
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "📊 *РЕЕСТР СТРАТЕГИЙ (LIVE REGISTRY)*" in call_args
