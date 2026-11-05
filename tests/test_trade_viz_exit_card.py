from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageColor

from astra_bot.decision.trade_viz import (
    CARD_BG,
    _default_exit_card_background,
    derive_exit_price,
    leveraged_roi_pct,
    render_exit_stats_card,
)

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
        assert all(abs(value - 200.0) < 0.1 for value in rendered.info["dpi"])


def test_png_smoke_with_profit_art():
    qty = 87.4861 * 100 * 6 / (94.679 * 1.50)
    png = render_exit_stats_card(
        symbol="SOL-USDT", side="short", entry=94.679, exit_price=94.443,
        pnl=87.4861, qty=qty, leverage=6, closed_at=1793652000,
        art_path=ASSETS / "card_bg_plus.png",
    )
    _assert_png(png)


def test_png_smoke_without_art_fallback():
    png = render_exit_stats_card(
        symbol="HYPE-USDT", side="long", entry=86.713, exit_price=86.553,
        pnl=-92.2984, qty=92.2984 * 100 * 6 / (86.713 * 1.10),
        leverage=6, closed_at=1793652000, art_path=ASSETS / "missing.png",
    )
    _assert_png(png)



def test_derive_exit_price_long_from_reconciled_pnl():
    exit_price = derive_exit_price(
        side="long", entry=2300, pnl=-507.614, qty=10.54448183,
    )
    assert abs(exit_price - 2251.86) < 0.01


def test_derive_exit_price_short_zero_pnl():
    exit_price = derive_exit_price(side="short", entry=7.039, pnl=0, qty=3479)
    assert exit_price == 7.039


def test_derive_exit_price_rejects_zero_qty():
    import pytest

    with pytest.raises(ValueError):
        derive_exit_price(side="long", entry=2300, pnl=-1, qty=0)


def test_default_card_background_follows_pnl_sign_not_position_side():
    assert _default_exit_card_background(True).name == "card_bg_plus.png"
    assert _default_exit_card_background(False).name == "card_bg_minus.png"


def test_owner_background_is_full_bleed_not_card_bg():
    qty = 87.4861 * 100 * 6 / (94.679 * 1.50)
    png = render_exit_stats_card(
        symbol="SOL-USDT", side="short", entry=94.679, exit_price=94.443,
        pnl=87.4861, qty=qty, leverage=6, closed_at=1793652000,
    )
    import io

    with Image.open(io.BytesIO(png)) as rendered:
        assert rendered.size == (1240, 1640)
        assert all(abs(value - 200.0) < 0.1 for value in rendered.info["dpi"])
        assert rendered.convert("RGB").getpixel((0, 0)) != ImageColor.getrgb(CARD_BG)


def test_missing_owner_background_keeps_dark_card_fallback():
    png = render_exit_stats_card(
        symbol="HYPE-USDT", side="long", entry=86.713, exit_price=86.553,
        pnl=-92.2984, qty=92.2984 * 100 * 6 / (86.713 * 1.10),
        leverage=6, closed_at=1793652000, art_path=ASSETS / "missing.png",
    )
    import io

    with Image.open(io.BytesIO(png)) as rendered:
        assert rendered.convert("RGB").getpixel((0, 0)) == ImageColor.getrgb(CARD_BG)
