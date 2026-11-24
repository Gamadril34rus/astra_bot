"""Pattern 4H breakout → LTF retest (DEMO / paper).

Middle profile from research (chat Nov 2026):
- Signal: 4h geometric patterns (falling/rising wedge, sym/desc triangle)
- Entry: retest after breakout (prefer 15m/1h bars when provided as candles)
- Session: 16:00–06:00 Europe/Moscow (13:00–03:00 UTC approx)
- Stop: structure extreme; TP: pattern height (measured move)
- Risk sizing is done by risk engine (target ~1.5% equity → ~5% on ~3R wins)

enabled=True only for paper/demo configs. Do not enable on REAL without owner sign-off.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..core import models
from .base import BaseStrategy, Signal, SignalType, StrategyConfig

logger = logging.getLogger(__name__)

_SESSION_UTC_START = 13
_SESSION_UTC_END = 3


def _in_session_utc(ts_sec: int | float | None) -> bool:
    if ts_sec is None:
        return True
    try:
        dt = datetime.fromtimestamp(int(ts_sec), tz=timezone.utc)
    except (OSError, ValueError, TypeError, OverflowError):
        return True
    h = dt.hour
    return h >= _SESSION_UTC_START or h < _SESSION_UTC_END


def _pivot_highs(h: list[float], w: int = 2) -> list[float | None]:
    n = len(h)
    out: list[float | None] = [None] * n
    for i in range(w, n - w):
        window = h[i - w : i + w + 1]
        if h[i] == max(window):
            out[i] = h[i]
    return out


def _pivot_lows(l: list[float], w: int = 2) -> list[float | None]:
    n = len(l)
    out: list[float | None] = [None] * n
    for i in range(w, n - w):
        window = l[i - w : i + w + 1]
        if l[i] == min(window):
            out[i] = l[i]
    return out


def _linreg(xs: list[int], ys: list[float]) -> tuple[float, float] | None:
    n = len(xs)
    if n < 2:
        return None
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-18:
        return None
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n
    return b, a


@dataclass
class Pattern4hRetestConfig(StrategyConfig):
    name: str = "pattern_4h_retest"
    enabled: bool = True
    preferred_timeframe: str = "4h"
    lookback: int = 30
    pivot_width: int = 2
    flat_tol: float = 0.006
    converge_ratio: float = 0.75
    retest_frac: float = 0.25
    max_wait_bars: int = 12
    min_rr: float = 0.8
    max_risk_pct: float = 0.08
    min_risk_pct: float = 0.002
    session_filter: bool = True
    patterns: list[str] = field(
        default_factory=lambda: [
            "falling_wedge",
            "sym_triangle",
            "rising_wedge",
            "desc_triangle",
        ]
    )


class Pattern4hRetestStrategy(BaseStrategy[Pattern4hRetestConfig]):
    """4h pattern breakout + retest entry (middle research profile)."""

    preferred_timeframe: str = "4h"

    def __init__(self, config: Pattern4hRetestConfig | None = None):
        super().__init__(config or Pattern4hRetestConfig())
        self.config: Pattern4hRetestConfig
        self.preferred_timeframe = getattr(self.config, "preferred_timeframe", "4h")
        self._pending: dict[str, list[dict[str, Any]]] = {}

    async def evaluate(
        self,
        symbol: str,
        candles: list[models.Candle],
        orderbook=None,
        current_price: float | None = None,
        market_regime: str | None = None,
    ) -> Signal | None:
        if self.should_skip_signal():
            return None
        c = self.config
        need = c.lookback + c.max_wait_bars + 5
        if not candles or len(candles) < need:
            return None

        highs = [float(x.high) for x in candles]
        lows = [float(x.low) for x in candles]
        closes = [float(x.close) for x in candles]
        opens = [float(x.open) for x in candles]
        times: list[int] = []
        for x in candles:
            t = int(getattr(x, "open_time", 0) or 0)
            if t > 10_000_000_000:
                t //= 1000
            times.append(t)

        last_i = len(closes) - 1
        signal_i = last_i - 1 if last_i >= 1 else last_i
        if signal_i < c.lookback:
            return None

        self._ingest_breakouts(symbol, highs, lows, closes, times, signal_i)

        pending = self._pending.get(symbol, [])
        if not pending:
            return None

        still: list[dict[str, Any]] = []
        chosen: dict[str, Any] | None = None
        for p in pending:
            age = last_i - int(p["bar_i"])
            if age < 1:
                still.append(p)
                continue
            if age > c.max_wait_bars:
                continue
            side = int(p["side"])
            stop = float(p["stop"])
            target = float(p["target"])
            entry_ref = float(p["entry_ref"])
            if side == 1:
                if lows[last_i] <= stop:
                    continue
            else:
                if highs[last_i] >= stop:
                    continue

            risk = abs(entry_ref - stop)
            if risk <= 0:
                continue

            retest_ok = False
            if side == 1:
                touched = lows[last_i] <= entry_ref - c.retest_frac * risk
                dir_candle = closes[last_i] > opens[last_i]
                in_zone = stop < closes[last_i] < target
                retest_ok = touched and dir_candle and in_zone
            else:
                touched = highs[last_i] >= entry_ref + c.retest_frac * risk
                dir_candle = closes[last_i] < opens[last_i]
                in_zone = target < closes[last_i] < stop
                retest_ok = touched and dir_candle and in_zone

            if retest_ok:
                if c.session_filter and not _in_session_utc(times[last_i]):
                    still.append(p)
                    continue
                chosen = p
                continue
            still.append(p)

        self._pending[symbol] = still
        if chosen is None:
            return None

        side = int(chosen["side"])
        stop = float(chosen["stop"])
        target = float(chosen["target"])
        entry = float(closes[last_i])
        if side == 1 and not (stop < entry < target):
            return None
        if side == -1 and not (target < entry < stop):
            return None

        risk_pct = abs(entry - stop) / entry if entry else 0
        if risk_pct < c.min_risk_pct or risk_pct > c.max_risk_pct:
            return None
        rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
        if rr < c.min_rr:
            return None

        direction = models.TradeDirection.LONG if side == 1 else models.TradeDirection.SHORT
        conf = min(0.9, 0.5 + 0.1 * min(rr, 3.0))
        signal = Signal(
            symbol=symbol,
            strategy_name=self.name,
            signal_type=SignalType.MOMENTUM,
            direction=direction,
            entry_price=Decimal(str(round(entry, 10))),
            stop_loss=Decimal(str(round(stop, 10))),
            take_profit=Decimal(str(round(target, 10))),
            position_size=Decimal("0"),
            risk_amount=Decimal("0"),
            confidence=conf,
            market_regime=market_regime or "UNKNOWN",
            features={
                "pattern": 1.0,
                "rr": float(round(rr, 3)),
                "risk_pct": float(round(risk_pct * 100, 3)),
                "side": float(side),
            },
        )
        pname = str(chosen.get("pattern", ""))
        signal.features["pattern_code"] = float(hash(pname) % 10000) / 10000.0
        logger.info(
            "pattern_4h_retest %s %s pattern=%s entry=%s stop=%s tp=%s rr=%.2f",
            symbol,
            direction.value,
            pname,
            entry,
            stop,
            target,
            rr,
        )
        return signal

    def _ingest_breakouts(
        self,
        symbol: str,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        times: list[int],
        i: int,
    ) -> None:
        c = self.config
        found = self._detect_at(highs, lows, closes, i)
        if not found:
            return
        pending = self._pending.setdefault(symbol, [])
        existing_keys = {(p["pattern"], p["side"], int(p["bar_i"])) for p in pending}
        for s in found:
            if s["pattern"] not in c.patterns:
                continue
            key = (s["pattern"], s["side"], i)
            if key in existing_keys:
                continue
            close = float(closes[i])
            stop = float(s["stop"])
            target = float(s["target"])
            risk = abs(close - stop) / close if close else 0
            reward = abs(target - close) / close if close else 0
            if risk < c.min_risk_pct or risk > c.max_risk_pct:
                continue
            if risk > 0 and reward / risk < c.min_rr:
                continue
            pending.append(
                {
                    "pattern": s["pattern"],
                    "side": s["side"],
                    "entry_ref": close,
                    "stop": stop,
                    "target": target,
                    "bar_i": i,
                    "signal_ts": times[i] if i < len(times) else 0,
                }
            )
        if len(pending) > 20:
            self._pending[symbol] = pending[-20:]

    def _detect_at(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        i: int,
    ) -> list[dict[str, Any]]:
        c = self.config
        lb = c.lookback
        if i < lb or i >= len(closes):
            return []
        h = highs[i - lb : i + 1]
        l = lows[i - lb : i + 1]
        cl = closes[i - lb : i + 1]
        ph = _pivot_highs(h, c.pivot_width)
        pl = _pivot_lows(l, c.pivot_width)
        ph_idx = [j for j, v in enumerate(ph) if v is not None]
        pl_idx = [j for j, v in enumerate(pl) if v is not None]
        if len(ph_idx) < 2 or len(pl_idx) < 2:
            return []
        close = float(cl[-1])
        prev = float(cl[-2])
        out: list[dict[str, Any]] = []

        def add(name: str, side: int, stop: float, target: float) -> None:
            if side == 1 and stop < close < target:
                out.append({"pattern": name, "side": side, "stop": stop, "target": target})
            if side == -1 and target < close < stop:
                out.append({"pattern": name, "side": side, "stop": stop, "target": target})

        if len(pl_idx) >= 2 and len(ph_idx) >= 3:
            rpl = pl_idx[-3:] if len(pl_idx) >= 3 else pl_idx
            rph = ph_idx[-4:] if len(ph_idx) >= 4 else ph_idx
            bot = sum(l[j] for j in rpl) / len(rpl)
            if bot > 0:
                std = (sum((l[j] - bot) ** 2 for j in rpl) / len(rpl)) ** 0.5
                if std / bot < c.flat_tol:
                    reg = _linreg(rph, [h[j] for j in rph])
                    if reg is not None and reg[0] < 0 and close < bot and prev >= bot:
                        stop = float(h[rph[-1]])
                        height = (reg[0] * rph[0] + reg[1]) - bot
                        target = bot - max(height, bot * 0.01)
                        add("desc_triangle", -1, stop, target)

        if len(ph_idx) >= 3 and len(pl_idx) >= 3:
            rh = ph_idx[-4:] if len(ph_idx) >= 4 else ph_idx
            rl = pl_idx[-4:] if len(pl_idx) >= 4 else pl_idx
            rh_reg = _linreg(rh, [h[j] for j in rh])
            rl_reg = _linreg(rl, [l[j] for j in rl])
            if rh_reg and rl_reg and rh_reg[0] < -1e-8 and rl_reg[0] > 1e-8:
                w0 = (rh_reg[0] * rh[0] + rh_reg[1]) - (rl_reg[0] * rl[0] + rl_reg[1])
                w1 = (rh_reg[0] * rh[-1] + rh_reg[1]) - (rl_reg[0] * rl[-1] + rl_reg[1])
                if w0 > 0 and w1 > 0 and w1 < w0 * c.converge_ratio:
                    upper = rh_reg[0] * (len(h) - 1) + rh_reg[1]
                    lower = rl_reg[0] * (len(h) - 1) + rl_reg[1]
                    if close > upper and prev <= upper:
                        add("sym_triangle", 1, float(lower), close + w0)
                    elif close < lower and prev >= lower:
                        add("sym_triangle", -1, float(upper), close - w0)

        if len(ph_idx) >= 3 and len(pl_idx) >= 3:
            rh = ph_idx[-4:] if len(ph_idx) >= 4 else ph_idx
            rl = pl_idx[-4:] if len(pl_idx) >= 4 else pl_idx
            rh_reg = _linreg(rh, [h[j] for j in rh])
            rl_reg = _linreg(rl, [l[j] for j in rl])
            if rh_reg and rl_reg and rh_reg[0] > 0 and rl_reg[0] > 0:
                w0 = (rh_reg[0] * rh[0] + rh_reg[1]) - (rl_reg[0] * rl[0] + rl_reg[1])
                w1 = (rh_reg[0] * rh[-1] + rh_reg[1]) - (rl_reg[0] * rl[-1] + rl_reg[1])
                if w0 > 0 and w1 > 0 and w1 < w0 * 0.8:
                    lower = rl_reg[0] * (len(h) - 1) + rl_reg[1]
                    if close < lower and prev >= lower:
                        stop = float(h[rh[-1]])
                        if stop > close:
                            add("rising_wedge", -1, stop, close - w0)

        if len(ph_idx) >= 3 and len(pl_idx) >= 3:
            rh = ph_idx[-4:] if len(ph_idx) >= 4 else ph_idx
            rl = pl_idx[-4:] if len(pl_idx) >= 4 else pl_idx
            rh_reg = _linreg(rh, [h[j] for j in rh])
            rl_reg = _linreg(rl, [l[j] for j in rl])
            if rh_reg and rl_reg and rh_reg[0] < 0 and rl_reg[0] < 0:
                w0 = (rh_reg[0] * rh[0] + rh_reg[1]) - (rl_reg[0] * rl[0] + rl_reg[1])
                w1 = (rh_reg[0] * rh[-1] + rh_reg[1]) - (rl_reg[0] * rl[-1] + rl_reg[1])
                if w0 > 0 and w1 > 0 and w1 < w0 * 0.8:
                    upper = rh_reg[0] * (len(h) - 1) + rh_reg[1]
                    if close > upper and prev <= upper:
                        stop = float(l[rl[-1]])
                        if stop < close:
                            add("falling_wedge", 1, stop, close + w0)

        return out

    def calculate_stop_loss(self, entry_price, candles, atr=None):
        return entry_price * (Decimal("1") - Decimal("0.015"))

    def calculate_take_profit(self, entry_price, stop_loss, candles):
        risk = abs(entry_price - stop_loss)
        return [
            {"price": entry_price + risk * Decimal("1"), "r_multiple": 1.0},
            {"price": entry_price + risk * Decimal("3"), "r_multiple": 3.0},
        ]
