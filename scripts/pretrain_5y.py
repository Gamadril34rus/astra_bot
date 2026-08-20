#!/usr/bin/env python3
"""Helpers for bounded, month-by-month ASTRA historical pretraining.

Историческое новостное обогащение использует бесплатный **GDELT DOC 2.0**
(без API-ключа). Ранее тут применялся платный NewsAPI (``NEWS_API_KEY``) —
он удалён. GDELT DOC официально хранит архив ~3 месяца, поэтому для более
старых окон кэш новостей остаётся пустым (нейтральный сентимент), а сбои
сети изолированы и не валят пятилетний прогон.

Реальные деньги и боевые ордера в этом скрипте не используются — он
готовит признаки только для research и OKX Demo/paper-торговли.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from astra_bot.adapters.okx import OKXClient
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.ml.historical_training import (
    OKXRateLimiter,
    fetch_historical_candles,
)
from astra_bot.ml.news_features import (
    ASSET_ALIASES,
    NewsFeatureService,
    NewsSnapshot,
    score_text,
)
from astra_bot.ml.news_sources import (
    NewsSources,
    historical_window_supported,
)

LOGGER = logging.getLogger("pretrain_5y")


def resample_candles(candles, hours: int):
    from collections import OrderedDict

    from astra_bot.core import models

    buckets: OrderedDict[int, list] = OrderedDict()
    bucket_ms = hours * 60 * 60 * 1000
    for candle in candles:
        buckets.setdefault(
            (int(candle.open_time) // bucket_ms) * bucket_ms, []
        ).append(candle)
    out = []
    for ts, rows in buckets.items():
        if rows:
            out.append(
                models.Candle(
                    exchange="okx",
                    symbol=rows[0].symbol,
                    timeframe=f"{hours}h" if hours < 24 else "1d",
                    open_time=ts,
                    open=rows[0].open,
                    high=max(r.high for r in rows),
                    low=min(r.low for r in rows),
                    close=rows[-1].close,
                    volume=sum((r.volume for r in rows), Decimal("0")),
                    quote_volume=sum(
                        (r.quote_volume for r in rows), Decimal("0")
                    ),
                    trades_count=sum(
                        getattr(r, "trades_count", 0) or 0 for r in rows
                    ),
                )
            )
    return out


async def _fetch_symbol(
    client, symbol, days, limiter, end_time_ms=None
):
    try:
        bars = await fetch_historical_candles(
            client=client,
            symbol=symbol.replace("/", "-"),
            timeframe="1h",
            lookback_days=days,
            limiter=limiter,
            end_time_ms=end_time_ms,
        )
        for bar in bars:
            bar.symbol = symbol
        LOGGER.info("history %s: %d candles", symbol, len(bars))
        return symbol, bars
    except Exception as exc:
        LOGGER.exception("history failed %s: %s", symbol, exc)
        return symbol, []


async def fetch_history(days: int, end_time_ms: int | None = None) -> dict[str, list]:
    """Fetch one finite window for every symbol through one shared limiter."""
    client = OKXClient(
        {
            "api_key": "",
            "api_secret": "",
            "sandbox": False,
            "enabled": True,
            "rate_limit_qps": 1.0,
        }
    )
    limiter = OKXRateLimiter(0.9)
    await client.initialize()
    try:
        results = await asyncio.gather(
            *[
                _fetch_symbol(client, symbol, days, limiter, end_time_ms)
                for symbol in TRADING_UNIVERSE
            ]
        )
        return {symbol: bars for symbol, bars in results}
    finally:
        await client.close()


def _news_query_text() -> str:
    return "crypto bitcoin ethereum blockchain regulation ETF"


async def build_monthly_news_cache(
    path: Path,
    years: int,
    month_start=None,
    month_end=None,
) -> None:
    """Построить месячный кэш новостей через бесплатный GDELT DOC 2.0.

    GDELT DOC отдаёт архив примерно за последние 3 месяца. Для более старых
    месяцев функция просто логирует пропуск и оставляет кэш пустым — это
    ожидаемое поведение бесплатного источника, а не ошибка.
    """
    import aiohttp

    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:  # dateutil есть в requirements, но подстрахуемся
        LOGGER.error("python-dateutil требуется для pretrain")
        return

    start = month_start or (
        datetime.now(tz=UTC) - relativedelta(years=years)
    )
    end = month_end or datetime.now(tz=UTC)

    news = NewsFeatureService(path)
    sources = NewsSources(free_crypto_news_enabled=False)

    aliases = {a: set(w) for a, w in ASSET_ALIASES.items()}

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        cursor = start
        while cursor < end:
            month_end_dt = min(cursor + relativedelta(months=1), end)
            if not historical_window_supported(cursor, month_end_dt):
                LOGGER.info(
                    "GDELT: месяц %s вне архива (~3 мес) — новости пропущены",
                    cursor.strftime("%Y-%m"),
                )
                cursor = month_end_dt
                continue

            # Глобальный запрос по рынку для фонового сентимента.
            global_query = (
                '("bitcoin" OR "ethereum" OR "crypto" OR "blockchain")'
            )
            global_res = await sources.gdelt_articles(
                session,
                global_query,
                start=cursor,
                end=month_end_dt,
                maxrecords=250,
            )
            global_tones = [
                a.tone for a in global_res.articles if a.tone is not None
            ]
            if not global_tones:
                global_tones = [
                    score_text(a.text)
                    for a in global_res.articles
                    if a.text
                ]
            gs = NewsSnapshot(
                sentiment=(
                    sum(global_tones) / len(global_tones)
                    if global_tones
                    else 0.0
                ),
                volume=min(1.0, len(global_tones) / 100),
                confidence=min(1.0, len(global_tones) / 30),
                shock=0.0,
                source="gdelt",
                articles=len(global_tones),
            )
            key = cursor.strftime("%Y-%m")
            news._cache[f"GLOBAL:{key}"] = gs.__dict__

            # По каждому символу — свой запрос (узко, чтобы не упираться в
            # лимит maxrecords GDELT на общий запрос).
            for symbol in TRADING_UNIVERSE:
                asset = symbol.split("/")[0]
                names = aliases.get(asset, {asset.lower()})
                # GDELT не любит длинные OR-цепочки — берём до 3 алиасов.
                terms = " OR ".join(f'"{n}"' for n in list(names)[:3])
                res = await sources.gdelt_articles(
                    session,
                    terms,
                    start=cursor,
                    end=month_end_dt,
                    maxrecords=100,
                )
                tones = [a.tone for a in res.articles if a.tone is not None]
                if not tones:
                    tones = [
                        score_text(a.text)
                        for a in res.articles
                        if a.text
                    ]
                snapshot = NewsSnapshot(
                    sentiment=(
                        sum(tones) / len(tones) if tones else gs.sentiment
                    ),
                    volume=(
                        min(1.0, len(tones) / 30) if tones else gs.volume
                    ),
                    confidence=(
                        min(1.0, len(tones) / 10) if tones else gs.confidence
                    ),
                    shock=0.0,
                    source="gdelt" if tones else "gdelt-fallback-global",
                    articles=len(tones),
                )
                news._cache[f"{symbol}:{key}"] = snapshot.__dict__

            LOGGER.info(
                "GDELT news cache for %s: %d global articles",
                key,
                len(global_tones),
            )
            cursor = month_end_dt

    news.save_cache()


if __name__ == "__main__":
    raise SystemExit("Используйте scripts/pretrain_research_runtime.py")
