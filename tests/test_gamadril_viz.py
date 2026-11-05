from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from astra_bot.decision import gamadril_viz, trade_viz
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


def _fake_aiohttp(calls: list[str]):
    class FormData:
        def add_field(self, *args, **kwargs) -> None:
            pass

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {"ok": True}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append(url)
            return Response()

    return SimpleNamespace(
        FormData=FormData,
        ClientSession=Session,
        ClientTimeout=lambda **kwargs: object(),
    )


def _notify_args() -> dict:
    return {
        "symbol": "BTC-USDT",
        "side": "long",
        "entry": 100.0,
        "exit_price": 105.0,
        "pnl": 5.0,
        "qty": 1.0,
        "leverage": 2.0,
        "virtual": True,
    }


def test_gamadril_exit_animation_preserves_frames_and_timing(tmp_path: Path) -> None:
    gif = tmp_path / "fixture.gif"
    _write_gif(gif)
    rendered = render_gamadril_exit_animation(
        **_notify_args(), animation_path=gif
    )
    result = Image.open(io.BytesIO(rendered))
    assert result.format == "GIF"
    assert result.n_frames == 2
    assert result.size == (960, 1280)
    durations = []
    for index in range(result.n_frames):
        result.seek(index)
        durations.append(result.info["duration"])
    assert durations == [100, 200]


def test_gamadril_exit_animation_missing_asset_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_gamadril_exit_animation(
            **_notify_args(), animation_path=tmp_path / "missing.gif"
        )


def test_exit_notification_falls_back_to_png_when_animation_is_missing(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp(calls))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(trade_viz, "render_exit_stats_card", lambda **kwargs: b"png")

    def missing_animation(**kwargs):
        raise FileNotFoundError("fixture: no animation")

    monkeypatch.setattr(gamadril_viz, "render_gamadril_exit_animation", missing_animation)
    sent = asyncio.run(trade_viz.notify_exit_stats_card(**_notify_args()))
    assert sent is True
    assert len(calls) == 1
    assert calls[0].endswith("/sendPhoto")


def test_exit_notification_prefers_animation_when_available(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "aiohttp", _fake_aiohttp(calls))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def unexpected_png(**kwargs):
        raise AssertionError("PNG should be rendered only on animation fallback")

    monkeypatch.setattr(trade_viz, "render_exit_stats_card", unexpected_png)
    monkeypatch.setattr(
        gamadril_viz, "render_gamadril_exit_animation", lambda **kwargs: b"gif"
    )
    sent = asyncio.run(trade_viz.notify_exit_stats_card(**_notify_args()))
    assert sent is True
    assert len(calls) == 1
    assert calls[0].endswith("/sendAnimation")

def test_exit_card_prefers_gamadril_master_background_and_keeps_legacy_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    assets = tmp_path / "assets"
    (assets / "gamadril").mkdir(parents=True)
    legacy = assets / "card_bg_plus.png"
    legacy.write_bytes(b"legacy")
    monkeypatch.setattr(trade_viz, "__file__", str(tmp_path / "trade_viz.py"))

    assert trade_viz._default_exit_card_background(True) == legacy

    gamadril = assets / "gamadril" / "mascot_card_bg.jpg"
    gamadril.write_bytes(b"gamadril")
    assert trade_viz._default_exit_card_background(False) == gamadril
