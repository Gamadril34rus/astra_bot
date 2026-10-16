"""LIVE TG digest 09:00/21:00 MSK — window, dedup, off-window skip."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from scripts.zeus_live_clock import _digest_window_key, _maybe_send_digest


class _Broker:
    def __init__(self, halt: str = ""):
        self._halt_reason = halt


class _Client:
    def __init__(self, balance: Decimal = Decimal("100006.64")):
        self._balance = balance

    async def get_balance_usdt(self) -> Decimal:
        return self._balance


def _write_exit(path: Path, pnl, ts: float) -> None:
    row = {"event": "exit", "symbol": "BTC-USDT", "side": "long", "pnl": pnl, "ts": ts}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def test_digest_window_keys():
    from datetime import datetime, timezone

    am = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc).timestamp()
    assert _digest_window_key(am) == "2026-10-16:am"
    pm = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc).timestamp()
    assert _digest_window_key(pm) == "2026-10-16:pm"
    noon = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc).timestamp()
    assert _digest_window_key(noon) is None


def test_digest_sends_once_in_window(tmp_path, monkeypatch):
    async def _():
        monkeypatch.chdir(tmp_path)
        trades = tmp_path / "models" / "zeus_live_trades.jsonl"
        trades.parent.mkdir(parents=True)
        digest = tmp_path / "models" / "zeus_live_digest.json"
        from datetime import datetime, timezone

        now = datetime(2026, 10, 16, 6, 5, tzinfo=timezone.utc).timestamp()
        msk_midnight_as_utc = datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc).timestamp()
        _write_exit(trades, 2.84, msk_midnight_as_utc + 3600)
        _write_exit(trades, -2.94, msk_midnight_as_utc + 7200)
        _write_exit(trades, 0.29, msk_midnight_as_utc + 9000)

        sent: list[str] = []

        async def _tg(msg: str) -> None:
            sent.append(msg)

        pos = tmp_path / "models" / "zeus_live_positions.json"
        pos.write_text(
            json.dumps(
                {
                    "positions": {
                        "BTC-USDT|long": {
                            "symbol": "BTC-USDT",
                            "side": "long",
                            "entry": "100",
                            "stop": "90",
                            "qty": "1",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        key1 = await _maybe_send_digest(
            _Broker(),
            _Client(),
            now_ts=now,
            digest_path=digest,
            trades_path=trades,
            send_tg=_tg,
        )
        assert key1 == "2026-10-16:am"
        assert len(sent) == 1
        assert "LIVE-СВОД" in sent[0]
        assert "утро" in sent[0]

        key2 = await _maybe_send_digest(
            _Broker(),
            _Client(),
            now_ts=now + 60,
            digest_path=digest,
            trades_path=trades,
            send_tg=_tg,
        )
        assert key2 is None
        assert len(sent) == 1

    asyncio.run(_())


def test_digest_skips_outside_window(tmp_path, monkeypatch):
    async def _():
        monkeypatch.chdir(tmp_path)
        from datetime import datetime, timezone

        noon = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc).timestamp()
        sent: list[str] = []

        async def _tg(msg: str) -> None:
            sent.append(msg)

        key = await _maybe_send_digest(
            _Broker(),
            _Client(),
            now_ts=noon,
            digest_path=tmp_path / "d.json",
            trades_path=tmp_path / "t.jsonl",
            send_tg=_tg,
        )
        assert key is None
        assert sent == []

    asyncio.run(_())
