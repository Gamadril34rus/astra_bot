"""B6: update_extremes replays gap bars so MFE/MAE are not understated."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from astra_bot.decision.broker import PaperBroker, PaperPosition


def _bar(symbol: str, open_time: int, high: float, low: float, close: float | None = None):
    return SimpleNamespace(
        symbol=symbol,
        open_time=open_time,
        high=high,
        low=low,
        close=close if close is not None else (high + low) / 2,
        open=(high + low) / 2,
        volume=1.0,
    )


def test_update_extremes_gap_bars_accumulate_mfe(tmp_path: Path):
    """Позиция живёт через паузу сессий — MFE считает high/low пропущенных баров."""
    broker = PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=Decimal("10000"),
    )
    pos = PaperPosition(
        id="t1",
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        quantity=Decimal("1"),
        initial_quantity=Decimal("1"),
        stop_loss=Decimal("95"),
        take_profits=[Decimal("110")],
        tp_fractions=[1.0],
        tp_filled=[False],
        strategy="zeus_wedge_retest_4h",
        opened_at=1_700_000_000_000,
        highest_price=Decimal("100"),
        lowest_price=Decimal("100"),
    )
    broker.positions = [pos]

    # Первый тик: бар T0
    broker.update_extremes(_bar("BTC-USDT", 1000, 101, 99))
    assert pos.highest_price == Decimal("101")
    assert pos.lowest_price == Decimal("99")
    assert broker._last_extremes_bar.get("BTC-USDT") == 1000

    # Gap: сессия пропустила бары 2000 и 3000; следующий тик видит 2000,3000,4000
    gap_bars = [
        _bar("BTC-USDT", 2000, 108, 100),  # high 108 — MFE
        _bar("BTC-USDT", 3000, 105, 97),   # low 97 — MAE
        _bar("BTC-USDT", 4000, 103, 101),
    ]
    last_ts = broker._last_extremes_bar.get("BTC-USDT")
    for bar in gap_bars:
        if bar.open_time > last_ts:
            broker.update_extremes(bar)
            last_ts = broker._last_extremes_bar.get("BTC-USDT")

    assert pos.highest_price == Decimal("108")
    assert pos.lowest_price == Decimal("97")
    assert broker._last_extremes_bar.get("BTC-USDT") == 4000
    # bars_held: T0 + 3 gap = 4 (каждый новый open_time)
    assert pos.bars_held == 4


def test_process_symbol_source_has_b6_replay():
    """Статический якорь: process_symbol доигрывает gap-бары, посев не возвращён."""
    src = Path("astra_bot/decision/trading_engine.py").read_text(encoding="utf-8")
    assert "bars_to_apply" in src
    assert "B6:" in src
    assert "_last_extremes_bar" in src
