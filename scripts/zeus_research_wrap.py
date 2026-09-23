"""Paper research filters for Zeus wedge (30-sym ~2y matched shape)."""
from __future__ import annotations

from typing import Any

from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)


def research_config() -> ZeusWedgeRetestConfig:
    return ZeusWedgeRetestConfig(
        enabled=True,
        lookback=36,
        max_width_pct=0.06,
        min_width_pct=0.008,
        min_rr=3.5,
        min_hold_bars_breakout=4,
        max_bars_true_breakout=6,
        enable_true_breakout=True,
        min_stop_pct=0.008,
        require_htf_bias=True,
        tp1_rr=2.0,
        tp2_rr=3.5,
        tp1_fraction=0.4,
        tp2_fraction=0.6,
    )


class ZeusMatchedResearchStrategy(ZeusWedgeRetestStrategy):
    """True-breakout only; falling→long, rising→short; HTF aligned."""

    def diagnose(
        self,
        candles: list,
        current_price: float | None = None,
    ) -> dict[str, Any]:
        d = super().diagnose(candles, current_price=current_price)
        if not d.get("would_signal"):
            return d
        pat = str(d.get("pattern") or "")
        direction = str(d.get("direction") or "")
        bias = str(d.get("htf_bias") or "")
        is_falling = bool(d.get("is_falling_wedge"))
        is_rising = bool(d.get("is_rising_wedge"))

        if "false_break" in pat:
            d["would_signal"] = False
            d["reject_reason"] = "false_break_disabled_research"
            d["stage"] = "filter"
            return d

        if direction == "long" or pat.startswith("true_breakout_up"):
            if bias != "up":
                d["would_signal"] = False
                d["reject_reason"] = "htf_not_aligned_up"
                d["stage"] = "bias"
                return d
            if not is_falling:
                d["would_signal"] = False
                d["reject_reason"] = "matched_shape_need_falling_for_long"
                d["stage"] = "structure"
                return d

        if direction == "short" or pat.startswith("true_breakout_down"):
            if bias != "down":
                d["would_signal"] = False
                d["reject_reason"] = "htf_not_aligned_down"
                d["stage"] = "bias"
                return d
            if not is_rising:
                d["would_signal"] = False
                d["reject_reason"] = "matched_shape_need_rising_for_short"
                d["stage"] = "structure"
                return d

        return d
