"""Exit Manager — обязательные safety-выходы с причинами (Этап 4).

Слой НАД ExitController и TP/SL/trailing/BREAKEVEN брокера (которые
уже закрывают позиции с reason): это обязательный «safety-контур»,
активный ВСЕГДА, независимо от гипотезы выхода:

1. ``MAX_HOLD`` — максимальное время удержания (по умолчанию 48 ч).
   Неотработавшая позиция — риск, а не актив.
2. ``VOL_EXPANSION`` — скачок волатильности (ATR vs медиана окна):
   выходим ДО того, как стоп вынесет спайком.
3. ``BTC_PANIC`` — обвал BTC (close 4h на X% ниже максимума highs 4h):
  .flatten ВСЕх позиций — корреляция крипты с BTC на крахе ≈ 1.

Каждый выход → ``ClosedTrade`` с ``exit_reason`` → урок
(``append_lessons`` подхватывает ``exit_reason`` из trade-дикта),
т.е. каждое закрытие — данные для исследования (TZ §5).
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

BTC_SYMBOL = "BTC-USDT"


@dataclass
class ExitManagerConfig:
    """Параметры safety-выходов. Значения — консервативные (survival > returns)."""

    max_hold_seconds: int = 48 * 3600  # 48 часов
    vol_expansion_ratio: float = 2.0  # TR_bара >= 2.0 × медиана TR → выход
    vol_lookback: int = 60  # мин. баров, чтобы считать серию ATR
    vol_window: int = 20  # окно медианы ATR
    btc_panic_drop_pct: float = 5.0  # BTC close ниже max(highs) на 5%
    btc_panic_bars: int = 30  # баров 4h для референса


def _true_range(o: float, h: float, lo: float, pc: float) -> float:
    return max(h - lo, abs(h - pc), abs(lo - pc))


def tr_series(candles: list[Any], lookback: int) -> list[float]:
    """Последние ``lookback`` значений True Range (True Range бара).

    True Range чувствительнее ATR к одиночному спайку (ATR(14) размазывает
    один широкий бар по окну 14), а именно спайк — то, что выносит стоп.
    """
    n = len(candles)
    start = max(1, n - lookback)
    out: list[float] = []
    for i in range(start, n):
        c = candles[i]
        pc = float(candles[i - 1].close)
        out.append(_true_range(float(c.open), float(c.high), float(c.low), pc))
    return out


class ExitManager:
    """Обязательные safety-выходы поверх контроллера выходов."""

    def __init__(
        self,
        okx: Any,
        broker: Any,
        config: ExitManagerConfig | None = None,
    ) -> None:
        self.okx = okx
        self.broker = broker
        self.config = config or ExitManagerConfig()
        self.btc_panic: bool = False

    # ------------------------------------------------------------ BTC panic
    async def refresh_btc_panic(self) -> bool:
        """Обновить флаг BTC-паники (раз в step, один запрос 4h).

        Ошибка сети → флаг НЕ сбрасывается принудительно в False
        fail-closed? Нет: при недоступных данных панику не объявляем
        (иначе любой сбой API флаттил бы портфель), но и текущий флаг
        при обвале не снимаем — сохраняем последнее известное значение.
        """
        cfg = self.config
        try:
            candles = await self.okx.get_candles(
                BTC_SYMBOL, timeframe="4h", limit=cfg.btc_panic_bars
            )
        except Exception as exc:
            logger.debug("BTC panic check (не сбрасываем флаг): %s", exc)
            return self.btc_panic
        if not candles or len(candles) < 10:
            return self.btc_panic
        closes = [float(c.close) for c in candles]
        highs = [float(c.high) for c in candles]
        ref = max(highs[:-1])  # максимум без текущего (ещё не закрытого) бара
        if ref <= 0:
            return self.btc_panic
        self.btc_panic = closes[-1] <= ref * (1.0 - cfg.btc_panic_drop_pct / 100.0)
        if self.btc_panic:
            logger.warning(
                "BTC PANIC: close 4h %.2f <= max(highs) %.2f × %.2f — "
                "флаттим все позиции",
                closes[-1],
                ref,
                1.0 - cfg.btc_panic_drop_pct / 100.0,
            )
        return self.btc_panic

    # ------------------------------------------------------------ per-symbol
    def check_symbol(
        self,
        positions: list[Any],
        candles_by_tf: dict[str, list[Any]],
        current_price: float,
        now_ms: int | None = None,
    ) -> list:
        """MAX_HOLD + VOL_EXPANSION по позициям (уже закрытые не трогаем).

        Возвращает список ClosedTrade (может быть пустым).
        """
        cfg = self.config
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        closed: list = []
        price = Decimal(str(current_price)) if current_price else None
        for pos in positions:
            reason = None
            # 1) MAX_HOLD
            if pos.opened_at and (now - int(pos.opened_at)) >= cfg.max_hold_seconds * 1000:
                reason = "MAX_HOLD"
            # 2) VOL_EXPANSION — по таймфрейму позиции (фолбэк на 1h):
            # TR текущего бара >= ratio × медиана TR предыдущего окна.
            if reason is None:
                tf = pos.timeframe or "1h"
                cs = candles_by_tf.get(tf) or candles_by_tf.get("1h")
                if cs and len(cs) >= cfg.vol_lookback:
                    series = tr_series(cs, cfg.vol_window)
                    if len(series) >= 5:
                        med = statistics.median(series[:-1])
                        last = series[-1]
                        if med > 0 and last >= cfg.vol_expansion_ratio * med:
                            reason = "VOL_EXPANSION"
            if reason and price is not None:
                trade = self.broker.close_position(pos.id, price, reason)
                if trade is not None:
                    logger.info(
                        "EXIT %s %s reason=%s price=%s",
                        pos.symbol, pos.id, reason, price,
                    )
                    closed.append(trade)
        return closed

    def flatten_symbol(self, symbol: str, current_price: float) -> list:
        """BTC PANIC: закрыть ВСЕ позиции символа с причиной BTC_PANIC."""
        price = Decimal(str(current_price)) if current_price else None
        if price is None:
            return []
        return self.broker.close_positions(symbol, price, "BTC_PANIC")
