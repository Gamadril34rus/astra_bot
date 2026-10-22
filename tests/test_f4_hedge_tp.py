"""F4: hedge-safe TP — no reduceOnly in request body; stop closePosition."""
from __future__ import annotations

from pathlib import Path


def _build_params(
    *,
    reduce_only: bool = False,
    close_position: bool = False,
    position_side: str | None = "SHORT",
    order_type: str = "TAKE_PROFIT_MARKET",
) -> dict:
    """Mirror client_live.place_order param assembly (no network)."""
    params: dict = {
        "symbol": "DOGE-USDT",
        "side": "BUY",
        "type": order_type.upper(),
    }
    params["quantity"] = 10.0
    if close_position:
        params["closePosition"] = "true"
    params["stopPrice"] = 0.07
    if position_side:
        params["positionSide"] = position_side.upper()
    if reduce_only:
        params["reduceOnly"] = "true"
    return params


def test_tp_body_omits_reduce_only():
    p = _build_params(reduce_only=False, close_position=False, position_side="SHORT")
    assert "reduceOnly" not in p
    assert p["positionSide"] == "SHORT"
    assert p["type"] == "TAKE_PROFIT_MARKET"


def test_stop_body_close_position_no_reduce_only():
    p = _build_params(
        reduce_only=False,
        close_position=True,
        position_side="SHORT",
        order_type="STOP_MARKET",
    )
    assert "reduceOnly" not in p
    assert p["closePosition"] == "true"


def test_reduce_only_true_still_sets_key():
    p = _build_params(reduce_only=True)
    assert p.get("reduceOnly") == "true"


def test_broker_tp_paths_use_reduce_only_false():
    src = Path("astra_bot/decision/live_broker.py").read_text(encoding="utf-8")
    assert "ro, cp = False, False  # F4: hedge forbids ReduceOnly (109400)" in src
    # residual True only inside reduce_position_qty
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if "reduce_only=True" not in ln:
            continue
        # walk up to function def
        chunk = "\n".join(lines[max(0, i - 80) : i + 1])
        assert "def reduce_position_qty" in chunk, f"L{i+1}: unexpected {ln.strip()}"
