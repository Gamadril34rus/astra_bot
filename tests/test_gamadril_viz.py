from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from astra_bot.decision.gamadril_viz import render_gamadril_exit_animation


def _write_gif(path: Path) -> None:
    frames = [
        Image.new("RGB", (64, 85), (20, 30, 40)),
        Image.new("RGB", (64, 85), (30, 40, 50)),
    ]
    frames[0].save(
        path, format="GIF", save_all=True, append_images=frames[1:],
        duration=[100, 200], loop=0,
    )


def test_gamadril_exit_animation_overlays_metrics_and_keeps_frames(tmp_path: Path) -> None:
    gif = tmp_path / "fixture.gif"
    _write_gif(gif)
    rendered = render_gamadril_exit_animation(
        symbol="BTC-USDT", side="long", entry=100.0, exit_price=105.0,
        pnl=5.0, qty=1.0, leverage=2.0, animation_path=gif,
    )
    result = Image.open(io.BytesIO(rendered))
    assert result.format == "GIF"
    assert result.n_frames == 2
    assert result.size == (960, 1280)


def test_gamadril_exit_animation_missing_asset_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_gamadril_exit_animation(
            symbol="BTC-USDT", side="short", entry=100.0, exit_price=95.0,
            pnl=5.0, qty=1.0, leverage=2.0, animation_path=tmp_path / "missing.gif",
        )
