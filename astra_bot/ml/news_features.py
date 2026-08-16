"""Новостной слой для ASTRA BOT.

Историческая новость может использовать NewsAPI при наличии ключа.
Для live-режима без ключа используется GDELT DOC 2.0 как бесплатный
fallback. Новость не является самостоятельным сигналом: она корректирует
вероятность входа и записывается в урок сделки.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import aiohttp

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

ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"), "BNB": ("binance coin", "bnb"),
    "XRP": ("xrp", "ripple"), "ADA": ("cardano", "ada"),
    "AVAX": ("avalanche", "avax"), "DOGE": ("dogecoin", "doge"),
    "LINK": ("chainlink", "link"), "DOT": ("polkadot", "dot"),
    "TRX": ("tron", "trx"), "LTC": ("litecoin", "ltc"),
    "BCH": ("bitcoin cash", "bch"), "ATOM": ("cosmos", "atom"),
    "NEAR": ("near protocol", "near"), "APT": ("aptos", "apt"),
    "ARB": ("arbitrum", "arb"), "OP": ("optimism", "op"),
    "SUI": ("sui",), "INJ": ("injective", "inj"),
    "TIA": ("celestia", "tia"), "FIL": ("filecoin", "fil"),
    "ICP": ("internet computer", "icp"), "HBAR": ("hedera", "hbar"),
    "AAVE": ("aave",), "UNI": ("uniswap", "uni"),
    "FET": ("fetch.ai", "fetch", "fet"), "TON": ("toncoin", "ton"),
    "XLM": ("stellar", "xlm"), "SHIB": ("shiba inu", "shib"),
    "PEPE": ("pepe",), "ETC": ("ethereum classic", "etc"),
    "CRO": ("crypto.com", "cronos", "cro"), "MKR": ("maker", "mkr"),
    "XMR": ("monero", "xmr"),
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
    words = set(re.findall(r"[a-zA-Z.]{3,}", text.lower()))
    pos = sum(1 for w in words if w in POSITIVE)
    neg = sum(1 for w in words if w in NEGATIVE)
    total = pos + neg
    return 0.0 if total == 0 else (pos - neg) / total


def _asset_query(symbol: str) -> str:
    asset = symbol.split("/")[0].upper()
    aliases = ASSET_ALIASES.get(asset, (asset.lower(),))
    return " OR ".join(f'"{a}"' for a in aliases[:3])


class NewsFeatureService:
    def __init__(self, cache_path: Path = Path("models/news_cache.json")) -> None:
        self.cache_path = cache_path
        self.news_api_key = os.getenv("NEWS_API_KEY", "").strip()
        self.timeout = aiohttp.ClientTimeout(total=15)
        self._cache: dict[str, Any] = {}
        if cache_path.exists():
            try:
                import json
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        import json
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def current(self, symbol: str) -> NewsSnapshot:
        """Получить новости за последние сутки."""
        if self.news_api_key:
            return await self._newsapi(symbol, days=1)
        return await self._gdelt(symbol)

    async def _newsapi(self, symbol: str, days: int) -> NewsSnapshot:
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=days)
        params = {
            "q": f'({_asset_query(symbol)}) AND (crypto OR blockchain)',
            "from": start.isoformat(), "to": end.isoformat(),
            "language": "en", "sortBy": "publishedAt", "pageSize": 100,
        }
        headers = {"X-Api-Key": self.news_api_key}
        url = "https://newsapi.org/v2/everything"
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        return NewsSnapshot()
                    data = await resp.json()
            articles = data.get("articles") or []
            if not articles:
                return NewsSnapshot(source="newsapi")
            scores = []
            for article in articles:
                text = f"{article.get('title', '')} {article.get('description', '')}"
                scores.append(_score_text(text))
            avg = sum(scores) / len(scores)
            confidence = min(1.0, len(scores) / 20.0)
            shock = avg * min(1.0, len(scores) / 50.0)
            return NewsSnapshot(
                sentiment=avg, volume=min(1.0, len(scores) / 100.0),
                shock=shock, confidence=confidence,
                source="newsapi", articles=len(scores),
            )
        except Exception:
            return NewsSnapshot()

    async def _gdelt(self, symbol: str) -> NewsSnapshot:
        query = quote_plus(f"({_asset_query(symbol)}) crypto")
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc?"
            f"query={query}&mode=timelinetone&timespan=1day&format=json"
        )
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return NewsSnapshot()
                    data = await resp.json(content_type=None)
            timelines = data.get("timeline") or []
            values = [float(p.get("value", 0.0)) for p in timelines if isinstance(p, dict)]
            if not values:
                return NewsSnapshot(source="gdelt")
            tone = sum(values) / len(values)
            normalized = max(-1.0, min(1.0, tone / 10.0))
            return NewsSnapshot(
                sentiment=normalized,
                volume=min(1.0, len(values) / 24.0),
                shock=normalized,
                confidence=min(1.0, len(values) / 24.0),
                source="gdelt",
                articles=len(values),
            )
        except Exception:
            return NewsSnapshot()

    def cached_historical(self, symbol: str, timestamp_ms: int) -> NewsSnapshot:
        """Вернуть сохранённый исторический news snapshot ближайшего часа."""
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        key = f"{symbol}:{dt.strftime('%Y-%m-%d-%H')}"
        row = self._cache.get(key)
        if not row:
            return NewsSnapshot()
        return NewsSnapshot(**row)
