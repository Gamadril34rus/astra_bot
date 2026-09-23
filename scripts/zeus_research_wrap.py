"""Paper research: matched wedge + channel capital + confidence for leverage."""
from __future__ import annotations

from typing import Any

from astra_bot.strategies.zeus_wedge_retest import (
    ZeusWedgeRetestConfig,
    ZeusWedgeRetestStrategy,
)
from astra_bot.strategies.zeus_channel_boundary import (
    ZeusChannelBoundaryConfig,
    ZeusChannelBoundaryStrategy,
)

from zeus_confidence import confidence_from_diag


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

    async def evaluate(
        self, symbol, candles, orderbook=None, current_price=None, market_regime=None, **kwargs
    ):
        sig = await super().evaluate(
            symbol,
            candles,
            orderbook=orderbook,
            current_price=current_price,
            market_regime=market_regime,
            **kwargs,
        )
        if sig is None:
            return None
        try:
            diag = self.diagnose(candles, current_price=current_price)
            conf = confidence_from_diag(diag, kind="wedge")
            sig.confidence = conf
            feats = dict(sig.features or {})
            feats["zeus_confidence"] = conf
            feats["leverage_eligible"] = True
            sig.features = feats
        except Exception:
            pass
        return sig


def channel_capital_config() -> ZeusChannelBoundaryConfig:
    """Capital research: TP2.0R, hold 2-5, width band for density."""
    return ZeusChannelBoundaryConfig(
        enabled=True,
        lookback=40,
        min_width_pct=0.01,
        max_width_pct=0.12,
        min_rr=2.0,
        enable_breakout_hold=True,
        min_hold_bars=2,
        max_hold_bars=5,
        min_stop_pct=0.008,
        require_htf_bias=True,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp1_fraction=0.4,
        tp2_fraction=0.6,
    )


class ZeusChannelCapitalStrategy(ZeusChannelBoundaryStrategy):
    """Channel break for capital density + confidence for leverage ladder."""

    async def evaluate(
        self, symbol, candles, orderbook=None, current_price=None, market_regime=None, **kwargs
    ):
        sig = await super().evaluate(
            symbol,
            candles,
            orderbook=orderbook,
            current_price=current_price,
            market_regime=market_regime,
            **kwargs,
        )
        if sig is None:
            return None
        try:
            price = (
                float(current_price)
                if current_price is not None
                else float(candles[-1].close)
            )
            diag = self.diagnose(candles, current_price=price)
            conf = confidence_from_diag(diag, kind="channel")
            sig.confidence = conf
            feats = dict(sig.features or {})
            feats["zeus_confidence"] = conf
            feats["leverage_eligible"] = True
            sig.features = feats
        except Exception:
            pass
        return sig
