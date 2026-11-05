from __future__ import annotations

import io

from PIL import Image

from astra_bot.decision.trade_viz import render_trade_chart


def _candles(n: int, step: float, start: float = 100.0) -> list[dict[str, float]]:
    rows = []
    price = start
    for i in range(n):
        op = price
        cl = price + (step if i % 2 == 0 else -step * 0.4)
        rows.append({"open_time": i, "open": op, "high": max(op, cl) + 1,
                     "low": min(op, cl) - 1, "close": cl, "volume": 10.0})
        price = cl
    return rows


def test_render_trade_chart_long_and_short_examples():
    bars4 = _candles(60, 0.7, 2300.0)
    bars1 = _candles(60, 0.2, 2300.0)
    long_png = render_trade_chart(
        bars4, bars1, symbol="ETH-USDT", side="long", entry=2300,
        stop=2233.86, target=2355.91, phase="ENTRY",
    )
    bars4_short = _candles(60, 0.7, 94.68)
    bars1_short = _candles(60, 0.2, 94.68)
    short_png = render_trade_chart(
        bars4_short, bars1_short, symbol="SOL-USDT", side="short", entry=94.68,
        stop=95.69, target=94.08, phase="ENTRY",
    )
    for rendered_bytes in (long_png, short_png):
        assert rendered_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(io.BytesIO(rendered_bytes)) as rendered:
            assert rendered.format == "PNG"
            assert rendered.size == (1560, 1040)
