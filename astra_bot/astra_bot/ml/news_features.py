"""Новостной слой для ASTRA BOT.

Источники новостей — только бесплатные, без платных ключей:

* **GDELT DOC 2.0** — основной источник для текущих и исторических новостей.
  DOC API хранит примерно 3 месяца архива; для более старых окон
  историческое обогащение остаётся пустым (это ожидаемый фолбэк, а не
  сбой).
* **Free Crypto News API** (cryptocurrency.cv) — дополнительный источник
  крипто-заголовков без ключа. Любой его сбой изолирован и не влияет на
  основной поток (безопасный fallback).

Ранее использовался платный NewsAPI (``NEWS_API_KEY``); он удалён. Ключ
больше не читается и не требуется.

Модуль отдаёт только признаки сентимента/объёма для research/paper-торговли.
Реальные деньги и боевые ордера к новостям не подключаются.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from .news_sources import (
    ASSET_ALIASES,
    NewsSources,
    aggregate_results,
    gdelt_asset_query,
    historical_window_supported,
    score_text,
)

logger = logging.getLogger(__name__)


def _free_crypto_news_default_enabled() -> bool:
    """Читает ``FREE_CRYPTO_NEWS_ENABLED`` (1/0/true/false). По умолчанию вкл."""
    raw = os.getenv("FREE_CRYPTO_NEWS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}

__all__ = [
    "ASSET_ALIASES",
    "NewsFeatureService",
    "NewsSnapshot",
    "score_text",
]

logger = logging.getLogger(__name__)

POSITIVE = {
    "approval", "approved", "adoption", "launch", "launched", "partnership",
    "upgrade", "growth", "surge", "bullish", "buy", "inflow", "record", "rally",
    "etf", "institutional", "integration", "listing", "profit", "breakout",
}
NEGATIVE = {
    "hack", "hacked", "exploit", "lawsuit", "ban", "banned", "fraud", "scam",
    "bearish", "sell", "outflow", "crash", "collapse", "liquidation", "delist",
    "delisted", "fine", "sanction", "attack", "bankruptcy", "panic", "rug",
}


@dataclass(frozen=True)
class NewsSnapshot:
    sentiment: float = 0.0
    volume: float = 0.0
    shock: float = 0.0
    confidence: float = 0.0
    source: str = "none"
    articles: int = 0

    def to_features(self) -> dict[str, float]:
        return {
            "news_sentiment": float(max(-1.0, min(1.0, self.sentiment))),
            "news_volume": float(max(0.0, self.volume)),
            "news_shock": float(max(-1.0, min(1.0, self.shock))),
            "news_confidence": float(max(0.0, min(1.0, self.confidence))),
        }


def _score_text(text: str) -> float:
    """Совместимость с историческими импортами. Делегирует в news_sources."""
    return score_text(text)


def _asset_query(symbol: str) -> str:
    return gdelt_asset_query(symbol, ASSET_ALIASES)


class NewsFeatureService:
    """Достаёт новостные признаки из бесплатных источников.

    GDELT — первичный источник; Free Crypto News — дополнение. Если оба
    источника недоступны, возвращается безопасный нейтральный снапшот
    (sentiment=0, confidence=0). Это не блокирует торговлю и не ломает
    research: модели просто видят «новостей нет».
    """

    def __init__(
        self,
        cache_path: Path = Path("models/news_cache.json"),
        *,
        free_crypto_news_enabled: bool | None = None,
        sources: NewsSources | None = None,
    ) -> None:
        self.cache_path = cache_path
        # Совместимость: раньше тут читался ``NEWS_API_KEY``. Теперь платных
        # ключей нет. Атрибут оставлен как пустая строка, чтобы старый код,
        # который мог его инспектировать, не падал с AttributeError.
        self.news_api_key: str = ""
        if free_crypto_news_enabled is None:
            free_crypto_news_enabled = _free_crypto_news_default_enabled()
        self.free_crypto_news_enabled = free_crypto_news_enabled
        self._sources = sources or NewsSources(
            free_crypto_news_enabled=free_crypto_news_enabled,
        )
        self.timeout = aiohttp.ClientTimeout(total=20)
        self._cache: dict[str, Any] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    # ------------------------------------------------------------- cache
    def save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.cache_path)

    def put_historical(
        self, symbol: str, timestamp: datetime, snapshot: NewsSnapshot
    ) -> None:
        self._cache[self._key(symbol, timestamp)] = snapshot.__dict__

    def cached_historical(self, symbol: str, timestamp_ms: int) -> NewsSnapshot:
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        for key in (
            self._key(symbol, dt),
            f"{symbol}:{dt.strftime('%Y-%m-%d')}",
            f"{symbol}:{dt.strftime('%Y-%m')}",
        ):
            row = self._cache.get(key)
            if row:
                try:
                    return NewsSnapshot(**row)
                except TypeError:
                    # Запись старого формата — игнорируем.
                    continue
        return NewsSnapshot()

    # ------------------------------------------------------------- current
    async def current(self, symbol: str) -> NewsSnapshot:
        """Текущий новостной снапшот для ``symbol``.

        GDELT первичен: тянем агрегированный ``timelinetone`` за сутки и
        немного статей. Параллельно опрашиваем Free Crypto News API как
        дополнение. Любая ошибка сети изолирована внутри ``NewsSources``.
        """
        query = f"({_asset_query(symbol)}) (crypto OR blockchain)"
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                primary, secondary = await self._query_current(session, symbol, query)
        except Exception as exc:
            logger.debug("News session failed for %s: %s", symbol, exc)
            return NewsSnapshot(source="fallback")

        sentiment, volume, shock, confidence, source, articles = aggregate_results(
            primary, secondary, asset_query=query
        )
        if source == "none":
            # Ничего не получили — безопасный нейтральный снапшот.
            return NewsSnapshot(source="fallback")
        return NewsSnapshot(
            sentiment=sentiment,
            volume=volume,
            shock=shock,
            confidence=confidence,
            source=source,
            articles=articles,
        )

    async def _query_current(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        query: str,
    ) -> tuple[Any, Any]:
        # GDELT: timeline tone (быстро) + небольшой artlist для подсчёта объёма.
        # Free Crypto News — параллельно, чтобы не удлинять критический путь.
        timeline = self._sources.gdelt_timeline(session, query, timespan="1d")
        articles = self._sources.gdelt_articles(session, query, maxrecords=50)
        free = self._sources.free_crypto_news(session, symbol, limit=50)
        tl, art, free_res = await __import__("asyncio").gather(
            timeline, articles, free, return_exceptions=True
        )
        # Если запрос упал совсем — нормализуем в пустой результат.
        primary_tone = tl if not isinstance(tl, Exception) else None
        primary_art = art if not isinstance(art, Exception) else None
        secondary = free_res if not isinstance(free_res, Exception) else None

        # Сливаем два GDELT-ответа в один первичный результат: aggregate_tone
        # из timeline, статьи из artlist.
        from .news_sources import SourceResult
        if primary_tone is None and primary_art is None:
            primary = SourceResult([], "gdelt", ok=False, note="exception")
        elif primary_tone is not None and primary_tone.ok and primary_tone.aggregate_tone is not None:
            primary = SourceResult(
                primary_art.articles if primary_art and primary_art.ok else [],
                "gdelt",
                aggregate_tone=primary_tone.aggregate_tone,
                ok=True,
            )
        else:
            # timeline пуст/упал, но artlist мог дать статьи с их собственным тоном.
            primary = primary_art or SourceResult([], "gdelt", ok=False, note="empty")
        if secondary is None:
            from .news_sources import SourceResult as _SR
            secondary = _SR([], "free_crypto_news", ok=False, note="exception")
        return primary, secondary

    # ------------------------------------------------------- historical
    async def fetch_historical_window(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        maxrecords: int = 100,
    ) -> NewsSnapshot:
        """Новостной снапшот за конкретное историческое окно.

        Основной источник — GDELT ``artlist`` с ``startdatetime``/
        ``enddatetime`` (бесплатно, без ключа). Free Crypto News API не
        отдаёт произвольные исторические окна, поэтому для исторического
        режима он не опрашивается.

        Если окно старше официального 3-месячного архива GDELT,
        возвращается нейтральный снапшот с ``source="gdelt-out-of-archive"``.
        """
        if not historical_window_supported(start, end):
            logger.debug(
                "GDELT window %s..%s is outside DOC archive; news kept neutral",
                start.date(),
                end.date(),
            )
            return NewsSnapshot(source="gdelt-out-of-archive")

        query = f"({_asset_query(symbol)}) (crypto OR blockchain)"
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                result = await self._sources.gdelt_articles(
                    session,
                    query,
                    start=start,
                    end=end,
                    maxrecords=maxrecords,
                )
        except Exception as exc:
            logger.debug("GDELT historical fetch failed: %s", exc)
            return NewsSnapshot(source="fallback")

        if not result.ok or not result.articles:
            note = result.note or "empty"
            return NewsSnapshot(source=f"gdelt-{note}")

        tones = [a.tone for a in result.articles if a.tone is not None]
        if not tones:
            # У статей нет собственного тона — считаем локально.
            tones = [score_text(a.text) for a in result.articles if a.text]
        if not tones:
            return NewsSnapshot(source="gdelt", articles=len(result.articles))
        avg = sum(tones) / len(tones)
        sentiment = max(-1.0, min(1.0, avg))
        volume = min(1.0, len(tones) / 75.0)
        return NewsSnapshot(
            sentiment=sentiment,
            volume=volume,
            shock=sentiment * min(1.0, len(tones) / 25.0),
            confidence=min(1.0, len(tones) / 20.0),
            source="gdelt",
            articles=len(result.articles),
        )

    # ------------------------------------------------------------- internal
    @staticmethod
    def _key(symbol: str, timestamp: datetime) -> str:
        return f"{symbol}:{timestamp.strftime('%Y-%m')}"
