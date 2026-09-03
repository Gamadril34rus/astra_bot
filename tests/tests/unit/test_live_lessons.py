"""Тесты записи реальных paper-сделок в уроки для ML."""


from astra_bot.ml.live_lessons import (
    append_lessons,
    merge_into_main_lessons,
    trade_to_lesson,
)


def _trade(pnl=10.0, reason="tp1", symbol="BTC-USDT"):
    return {
        "id": "t1",
        "symbol": symbol,
        "direction": "short",
        "entry_price": 63000.0,
        "exit_price": 62000.0,
        "quantity": 0.01,
        "pnl": pnl,
        "pnl_pct": 1.6,
        "exit_reason": reason,
        "strategy": "pullback",
        "opened_at": 1000,
        "closed_at": 2000,
        "notes": {"score": 0.8, "rr": 0.75},
    }


def test_win_loss_outcome():
    win = trade_to_lesson(_trade(pnl=5.0, reason="tp1"))
    loss = trade_to_lesson(_trade(pnl=-5.0, reason="stop_loss"))
    assert win["outcome"] == "win"
    assert loss["outcome"] == "loss"
    assert win["recommendation"] == "HOLD_WINNERS"
    assert loss["recommendation"] != "HOLD_WINNERS"


def test_stop_loss_recommendation():
    loss = trade_to_lesson(_trade(pnl=-5.0, reason="stop_loss"))
    assert loss["outcome"] == "loss"
    assert loss["recommendation"] in {"WIDEN_STOP_LOSS", "SKIP_FALSE_BREAKOUT"}


def test_append_and_merge(tmp_path):
    live = tmp_path / "live.jsonl"
    main = tmp_path / "lessons.jsonl"
    t1 = _trade(pnl=5.0, reason="tp1")
    t1["id"] = "t1"
    t2 = _trade(pnl=-3.0, reason="stop_loss")
    t2["id"] = "t2"
    append_lessons([t1, t2], path=live)
    assert live.read_text().count("\n") == 2

    added = merge_into_main_lessons(live_path=live, main_path=main)
    assert added == 2
    again = merge_into_main_lessons(live_path=live, main_path=main)
    assert again == 0
    assert main.read_text().count("\n") == 2
