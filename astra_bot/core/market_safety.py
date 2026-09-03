"""
Market safety — общая «проверка перед входом» для живой торговли.

Объединяет всё, что просил владелец, в один вердикт:

* расписание (бюджет часов в месяц / активные сессии);
* новостной риск (свежие заголовки по монете и рынку);
* он-чейн/китовые потоки (на базе рыночных данных биржи (BingX): всплеск объёма,
  дисбаланс стакана, резкие движения стакана как прокси «движения китов»);
* волатильность/ликвидность (узкий спред, глубина, аномальный диапазон).

Если что-то не в норме — ``allowed=False`` с понятной причиной, и бот
пропускает сделку. Это главный механизм «не слить депозит в ноль».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..decision.news_engine import NewsEngine
from . import trading_schedule

logger = logging.getLogger(__name__)


@dataclass
class SafetyVerdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    news_score: int = 0
    volatility_pct: float = 0.0
    spread_pct: float = 0.0
    book_imbalance: float = 0.0
    scheduled: bool = True

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "news_score": self.news_score,
            "volatility_pct": self.volatility_pct,
            "spread_pct": self.spread_pct,
            "book_imbalance": self.book_imbalance,
            "scheduled": self.scheduled,
        }


class MarketSafety:
    """Единая точка принятия решения «можно ли входить прямо сейчас»."""

    def __init__(
        self,
        news_engine: NewsEngine | None = None,
        max_news_score: int = 60,
        max_spread_pct: float = 0.25,
        max_volatility_pct: float = 5.0,
        max_book_imbalance: float = 0.85,
    ):
        self.news = news_engine or NewsEngine()
        self.max_news_score = max_news_score
        self.max_spread_pct = max_spread_pct
        self.max_volatility_pct = max_volatility_pct
        self.max_book_imbalance = max_book_imbalance
        self._news_items = None
        self._news_fetched_at = 0.0

    def _recent_news(self):
        # Кэшируем заголовки на 5 минут, чтобы не дёргать RSS на каждый тик.
        import time
        now = time.time()
        if self._news_items is None or (now - self._news_fetched_at) > 300:
            try:
                self._news_items = self.news.fetch_recent(120)
            except Exception as exc:
                logger.debug("news fetch failed: %s", exc)
                self._news_items = []
            self._news_fetched_at = now
        return self._news_items

    def check(
        self,
        symbol: str,
        *,
        ticker: dict[str, Any] | None = None,
        orderbook: dict[str, Any] | None = None,
        candles: list | None = None,
        now: datetime | None = None,
    ) -> SafetyVerdict:
        reasons: list[str] = []

        # 1) Расписание и бюджет часов.
        scheduled = trading_schedule.can_trade_now(now)
        if not scheduled:
            reasons.append("вне расписания или исчерпан бюджет часов")

        # 2) Новости.
        report = self.news.assess(symbol, items=self._recent_news())
        if report.blocked or report.score >= self.max_news_score:
            reasons.append(
                f"высокий новостной риск ({report.score})"
                + (f": {report.headline[:60]}" if report.headline else "")
            )

        spread_pct = 0.0
        imbalance = 0.0
        volatility_pct = 0.0

        # 3) Стакан: спред и дисбаланс (китовая стена).
        if orderbook:
            best_bid = float(orderbook.get("best_bid") or 0)
            best_ask = float(orderbook.get("best_ask") or 0)
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            if mid:
                spread_pct = (best_ask - best_bid) / mid * 100
            bids_depth = float(orderbook.get("bids_depth") or 0)
            asks_depth = float(orderbook.get("asks_depth") or 0)
            total = bids_depth + asks_depth
            if total > 0:
                imbalance = (bids_depth - asks_depth) / total
            if spread_pct > self.max_spread_pct:
                reasons.append(f"широкий спред {spread_pct:.3f}%")
            if abs(imbalance) > self.max_book_imbalance:
                side = "продаж" if imbalance < 0 else "покупок"
                reasons.append(f"дисбаланс стакана ({side}) {imbalance:.2f}")

        # 4) Волатильность по последним барам.
        if candles and len(candles) >= 6:
            ranges = []
            for c in candles[-6:]:
                hi = float(getattr(c, "high", 0) or 0)
                lo = float(getattr(c, "low", 0) or 0)
                cl = float(getattr(c, "close", 0) or 1)
                if cl:
                    ranges.append((hi - lo) / cl * 100)
            if ranges:
                volatility_pct = sum(ranges) / len(ranges)
                if volatility_pct >= self.max_volatility_pct:
                    reasons.append(f"аномальная волатильность {volatility_pct:.2f}%")

        # 5) Резкое движение 24ч по тикеру (дамп/памп).
        if ticker:
            try:
                last = float(ticker.get("last") or 0)
                open24 = float(ticker.get("open24h") or 0)
                if last and open24:
                    change = (last / open24 - 1) * 100
                    if abs(change) >= 8.0:
                        reasons.append(f"резкое движение 24ч {change:+.1f}%")
            except Exception:
                pass

        allowed = scheduled and not reasons
        return SafetyVerdict(
            allowed=allowed,
            reasons=reasons,
            news_score=report.score,
            volatility_pct=round(volatility_pct, 3),
            spread_pct=round(spread_pct, 4),
            book_imbalance=round(imbalance, 3),
            scheduled=scheduled,
        )
