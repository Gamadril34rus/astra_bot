from __future__ import annotations

from astra_bot.decision.trade_viz import render_trade_chart


def _candles(n: int, step: float) -> list[dict[str, float]]:
    rows = []
    price = 100.0
    for i in range(n):
        op = price
        cl = price + (step if i % 2 == 0 else -step * 0.4)
        rows.append({"open_time": i, "open": op, "high": max(op, cl) + 1,
                     "low": min(op, cl) - 1, "close": cl, "volume": 10.0})
        price = cl
    return rows


def test_render_trade_chart_long_and_short_examples():
    bars4 = _candles(60, 0.7)
    bars1 = _candles(60, 0.2)
    long_png = render_trade_chart(
        bars4, bars1, symbol="ETH-USDT", side="long", entry=2300,
        stop=2233.86, target=2355.91, phase="ENTRY",
    )
    short_png = render_trade_chart(
        bars4, bars1, symbol="SOL-USDT", side="short", entry=94.68,
        stop=95.69, target=94.08, phase="ENTRY",
    )
    assert long_png.startswith(b"\x89PNG")
    assert short_png.startswith(b"\x89PNG")
