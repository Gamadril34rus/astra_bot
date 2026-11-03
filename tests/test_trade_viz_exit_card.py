from __future__ import annotations

from pathlib import Path

from astra_bot.decision.trade_viz import leveraged_roi_pct, render_exit_stats_card
from PIL import Image

ASSETS = Path(__file__).parents[1] / "astra_bot" / "decision" / "assets"


def test_sol_short_reference_roi():
    qty = 87.4861 * 100 * 6 / (94.679 * 1.50)
    roi = leveraged_roi_pct(entry=94.679, pnl=87.4861, qty=qty, leverage=6)
    assert abs(roi - 1.50) < 0.005


def test_hype_long_reference_roi():
    qty = 92.2984 * 100 * 6 / (86.713 * 1.10)
    roi = leveraged_roi_pct(entry=86.713, pnl=-92.2984, qty=qty, leverage=6)
    assert abs(roi - (-1.10)) < 0.005


def _assert_png(data: bytes) -> None:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 1000
    import io
    with Image.open(io.BytesIO(data)) as rendered:
        assert rendered.format == "PNG"
        assert rendered.size == (1240, 1640)


def test_png_smoke_with_profit_art():
    qty = 87.4861 * 100 * 6 / (94.679 * 1.50)
    png = render_exit_stats_card(
        symbol="SOL-USDT", side="short", entry=94.679, exit_price=94.443,
        pnl=87.4861, qty=qty, leverage=6, closed_at=1793652000,
        art_path=ASSETS / "art_plus.png",
    )
    _assert_png(png)


def test_png_smoke_without_art_fallback():
    png = render_exit_stats_card(
        symbol="HYPE-USDT", side="long", entry=86.713, exit_price=86.553,
        pnl=-92.2984, qty=92.2984 * 100 * 6 / (86.713 * 1.10),
        leverage=6, closed_at=1793652000, art_path=ASSETS / "missing.png",
    )
    _assert_png(png)
