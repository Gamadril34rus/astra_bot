"""Фаза-0: тень сигналов Зевса (wedge false-break retest 4h).

На каждом тике движка оценивает ``ZeusWedgeRetestStrategy`` на ЗАКРЫТЫХ
4h-барах с ``enabled=True`` только внутри тени. В live/paper исполнение
НЕ идёт: стратегия в реестре TIER_RESEARCH, default enabled=False.

Журнал ``models/zeus_signal_shadow.jsonl`` — материал до среза 26–27.09:
сколько раз сработал бы паттерн, сторона, RR, границы клина.

Образец: D5 htf_shadow / pattern_exit_shadow — fail-open, best-effort,
дедуп по (symbol, bar_time, direction).

``install_on_engine`` — вешает тень на ``process_symbol`` из run_bot / AstraBot,
чтобы Actions каждые 5 мин копили данные без правки risk-контура.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PATH = "models/zeus_signal_shadow.jsonl"
SHADOW_LOG_LIMIT = 5_000


class ZeusSignalShadow:
    """Append-only журнал гипотетических входов Зевса (фаза 0)."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._seen: set[tuple[str, int, str]] = set()
        self._strategy = None

    def _get_strategy(self) -> Any:
        if self._strategy is None:
            from ..strategies.zeus_wedge_retest import (
                ZeusWedgeRetestConfig,
                ZeusWedgeRetestStrategy,
            )

            self._strategy = ZeusWedgeRetestStrategy(
                ZeusWedgeRetestConfig(enabled=True)
            )
        return self._strategy

    async def observe(
        self,
        *,
        symbol: str,
        candles_4h: list[Any],
        current_price: float | None = None,
        market_regime: str | None = None,
        ts_ms: int | None = None,
    ) -> bool:
        """Оценить Зевса на 4h; при сигнале дописать строку. True = записано."""
        if not candles_4h or len(candles_4h) < 28:
            return False
        closed = list(candles_4h[:-1]) if len(candles_4h) > 1 else list(candles_4h)
        if len(closed) < 24:
            return False
        bar_time = int(getattr(closed[-1], "open_time", 0) or 0)
        try:
            strat = self._get_strategy()
            signal = await strat.evaluate(
                symbol=symbol,
                candles=closed,
                current_price=current_price,
                market_regime=market_regime,
            )
        except Exception as exc:
            logger.debug("zeus_signal_shadow evaluate skipped: %s", exc)
            return False
        if signal is None:
            return False
        direction = (
            signal.direction.value
            if hasattr(signal.direction, "value")
            else str(signal.direction)
        )
        key = (symbol, bar_time, direction)
        if key in self._seen:
            return False
        try:
            feats = dict(getattr(signal, "features", None) or {})
            row = {
                "ts": int(ts_ms if ts_ms is not None else time.time() * 1000),
                "event": "zeus_shadow_signal",
                "symbol": symbol,
                "bar_time": bar_time,
                "strategy": getattr(signal, "strategy_name", "zeus_wedge_retest_4h"),
                "direction": direction,
                "entry_price": str(signal.entry_price),
                "stop_loss": str(signal.stop_loss),
                "take_profit": str(signal.take_profit),
                "rr": round(float(getattr(signal, "risk_reward_ratio", 0) or 0), 4),
                "confidence": float(getattr(signal, "confidence", 0) or 0),
                "regime": market_regime or "UNKNOWN",
                "features": feats,
                "would_execute": False,
                "note": "shadow-only until post-slice promote",
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._seen.add(key)
            return True
        except Exception as exc:
            logger.debug("zeus_signal_shadow write skipped: %s", exc)
            return False


def install_on_engine(engine: Any) -> bool:
    """Повесить тень Зевса на process_symbol.

    Идемпотентно. Вызывается из run_bot / AstraBot — Actions каждые 5 мин
    копят ``models/zeus_signal_shadow.jsonl`` без live-исполнения.
    """
    if getattr(engine, "_zeus_shadow_wrapped", False):
        return False
    cfg_on = bool(
        getattr(getattr(engine, "config", None), "zeus_signal_shadow_enabled", True)
    )
    if not cfg_on:
        engine.zeus_signal_shadow = None
        return False
    path = getattr(
        getattr(engine, "config", None), "zeus_signal_shadow_path", DEFAULT_PATH
    )
    engine.zeus_signal_shadow = ZeusSignalShadow(path)
    orig = engine.process_symbol

    async def _wrapped(symbol: str):
        result = await orig(symbol)
        try:
            shadow = engine.zeus_signal_shadow
            if shadow is None:
                return result
            ctx = await engine.fetch_context(symbol)
            c4 = (ctx.candles or {}).get("4h") or []
            await shadow.observe(
                symbol=symbol,
                candles_4h=list(c4),
                current_price=float(ctx.current_price),
                market_regime=None,
            )
        except Exception as exc:
            logger.debug("zeus_signal_shadow install hook: %s", exc)
        return result

    engine.process_symbol = _wrapped  # type: ignore[method-assign]
    engine._zeus_shadow_wrapped = True
    logger.info("Zeus signal shadow installed → %s", path)
    return True
