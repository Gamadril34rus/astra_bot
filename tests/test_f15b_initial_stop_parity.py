"""F15b: initial_stop semantics for TP clamp + cancel journal + warn on price-past."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from astra_bot.decision.exit_plan import clamp_take_rr
from astra_bot.decision.live_margin_warn import maybe_warn_margin_reject
from astra_bot.decision.live_positions import save_positions
from scripts.zeus_live_clock import apply_tp_parity_v1, migrate_initial_stop_v1


def test_post_be_take_from_initial_risk_valid_vs_mark():
    """After BE, clamp uses initial_stop not current stop → TP stays valid for short."""
    entry = Decimal("59.17")
    initial_stop = Decimal("61.20")  # wide risk at entry
    current_stop = Decimal("59.11")  # BE
    old_tp = Decimal("56.25")  # far TP
    side = "short"
    # RR from current stop is huge; from initial is moderate
    rr_cur = abs(old_tp - entry) / abs(entry - current_stop)
    rr_init = abs(old_tp - entry) / abs(entry - initial_stop)
    assert rr_cur > 10
    assert rr_init < 5
    new_tp = clamp_take_rr(entry, initial_stop, old_tp, side, 2.0, 2.3)
    # short: new_tp < entry and below a typical mark near entry
    mark = Decimal("58.50")
    assert new_tp < entry
    assert new_tp < mark  # exchange-valid for short TP


def test_migrate_ltc_from_parity_old_tp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    plans = {
        "LTC-USDT|short": {
            "symbol": "LTC-USDT",
            "side": "short",
            "entry": "59.17",
            "stop": "59.11",  # BE already
            "tp": "56.246",
            "be_moved": True,
            "qty": "474.7",
        }
    }
    save_positions(plans)
    # parity failed line with old_tp
    with open("models/zeus_live_journal.jsonl", "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event": "tp_parity_failed",
                    "symbol": "LTC-USDT",
                    "old_tp": "56.24605216463415",
                    "ts": 1,
                }
            )
            + "\n"
        )
    out = migrate_initial_stop_v1()
    assert out["migrated"] >= 1
    saved = json.loads(Path("models/zeus_live_positions.json").read_text())
    row = saved["positions"]["LTC-USDT|short"]
    assert row.get("initial_stop") is not None
    # risk ≈ |56.246-59.17|/1.5
    init = Decimal(row["initial_stop"])
    entry = Decimal("59.17")
    assert abs(init - entry) > Decimal("1.5")  # reconstructed wide stop


def test_fail_does_not_set_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    save_positions(
        {
            "DOGE-USDT|short": {
                "symbol": "DOGE-USDT",
                "side": "short",
                "entry": "0.07964",
                "stop": "0.07956",
                "initial_stop": "0.082",
                "tp": "0.073",  # ~2.8R — overshoot so parity attempts place (not early preserve)
                "qty": "100",
            }
        }
    )

    class Client:
        async def get_positions(self):
            return [SimpleNamespace(symbol="DOGE-USDT", quantity=Decimal("100"), side="short")]

        async def get_open_orders(self, symbol=None):
            return []

        async def place_order(self, **kw):
            raise RuntimeError("Take Profit price should be lower than the current price")

        async def cancel_order(self, symbol, oid):
            return True

    broker = MagicMock()
    broker.client = Client()
    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("failed", 0) >= 1
    saved = json.loads(Path("models/zeus_live_positions.json").read_text())
    assert not saved["positions"]["DOGE-USDT|short"].get("tp_parity_v1")
    assert any(j.get("event") == "tp_parity_failed" for j in journals)


def test_warn_on_price_past(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    sent = []
    assert maybe_warn_margin_reject(
        "LTC-USDT",
        "Take Profit price should be lower than the current price",
        lambda m: sent.append(m),
    )
    assert sent and "защита отклонена" in sent[0]
    # dedup
    assert not maybe_warn_margin_reject(
        "LTC-USDT",
        "Take Profit price should be lower than the current price",
        lambda m: sent.append(m),
    )
    assert len(sent) == 1


def test_order_cancel_on_parity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    journals = []
    monkeypatch.setattr(
        "astra_bot.decision.live_arm_wiring.append_journal",
        lambda row: journals.append(row),
    )
    monkeypatch.setattr(
        "astra_bot.decision.live_broker.append_live_trade",
        lambda row, path=None: None,
    )
    # long: TP still below mark path — successful place
    save_positions(
        {
            "BTC-USDT|long": {
                "symbol": "BTC-USDT",
                "side": "long",
                "entry": "100",
                "stop": "90",
                "initial_stop": "90",
                "tp": "140",  # 4R overshoot — parity must clamp+replace
                "qty": "10",
            }
        }
    )
    cancels = []

    class Client:
        async def get_positions(self):
            return [SimpleNamespace(symbol="BTC-USDT", quantity=Decimal("10"), side="long")]

        async def get_open_orders(self, symbol=None):
            return [
                SimpleNamespace(
                    order_type="TAKE_PROFIT_MARKET",
                    type="TAKE_PROFIT_MARKET",
                    id="old_tp",
                )
            ]

        async def place_order(self, **kw):
            return SimpleNamespace(id="new_tp")

        async def cancel_order(self, symbol, oid):
            cancels.append(oid)
            return True

    broker = MagicMock()
    broker.client = Client()
    out = asyncio.run(apply_tp_parity_v1(broker, send_tg=None))
    assert out.get("replaced", 0) >= 1
    assert "old_tp" in cancels
    assert any(j.get("event") == "order_cancel" for j in journals)
    assert any(j.get("event") == "tp_parity_replace" for j in journals)
