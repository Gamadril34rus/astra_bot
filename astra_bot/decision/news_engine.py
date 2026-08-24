"""
News engine — новостной риск-скоринг.

В отличие от стаб-версии, тянет реальные заголовки из бесплатных RSS
(без API-ключей): CryptoPanic (публичный поток), CoinDesk, CoinTelegraph.
Возвращает ``NewsReport`` со скор-ом 0..100 и флагом ``blocked``, если
прямо сейчас новостной импульс высокой важности (взлом, SEC, крупный дамп,
расследование) — тогда бот пропускает вход, чтобы не попасть под разнос.

Работает офлайн-фолбэком: если сеть недоступна, возвращает безопасный 0
(не блокирует торговлю, но и инициировать сделки по новостям не заставляет).
"""

from __future__ import annotations

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Бесплатные публичные RSS-фиды. CryptoPanic без токена отдаёт общий поток.
FEEDS = (
    "https://coinjournal.net/news/feed/",
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
)

# Ключевые слова, повышающие риск. Сгруппированы по весу.
HIGH_IMPACT = (
    "hack", "hacked", "exploit", "security breach", "rug pull", "scam",
    "sec sues", "sec charges", "lawsuit", "indictment", "arrest",
    "ftx", "bankruptcy", "insolvent", "collapse", "crash",
    "blackrock", "fed rate", "rate decision", "liquidation cascade",
)
MEDIUM_IMPACT = (
    "regulation", "ban", "outage", "downtime", "stablecoin depeg",
    "fork", "airdrop", "upgrade", "halving", "etf",
)

NEGATIVE_HINTS = (
    "plunge", "plunges", "tumbles", "sinks", "slumps", "drops", "dumps",
    "selloff", "sell-off", "falls", "bearish", "fears",
)

# Токены монет для определения, про какую монету новость.
COIN_TOKENS = (
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple",
    "dogecoin", "doge", "cardano", "ada", "avalanche", "avax", "chainlink",
    "link", "polkadot", "dot", "tron", "trx", "bnb", "ton", "shiba", "shib",
    "litecoin", "ltc", "uniswap", "uni", "harmony", "one",
)


def _mentions_other_coin(text_lower: str, coin_lower: str) -> bool:
    """True, если в тексте упоминается какая-то монета, отличная от нашей."""
    for tok in COIN_TOKENS:
        if tok != coin_lower and re.search(rf"\b{re.escape(tok)}\b", text_lower):
            return True
    return False


# Соответствие тикера и названия монеты (для поиска упоминаний в новостях).
_COIN_ALIASES = {
    "btc": ("btc", "bitcoin"),
    "eth": ("eth", "ethereum"),
    "sol": ("sol", "solana"),
    "xrp": ("xrp", "ripple"),
    "doge": ("doge", "dogecoin"),
    "ada": ("ada", "cardano"),
    "avax": ("avax", "avalanche"),
    "link": ("link", "chainlink"),
    "dot": ("dot", "polkadot"),
    "trx": ("trx", "tron"),
}


def _coin_mentioned(text_lower: str, coin_lower: str) -> bool:
    aliases = _COIN_ALIASES.get(coin_lower, (coin_lower,))
    return any(re.search(rf"\b{re.escape(a)}\b", text_lower) for a in aliases)

# Сколько минут новость считается «свежей».
FRESHNESS_MIN = 120
# Сколько заголовков тянем максимум с фида.
MAX_ITEMS = 15


@dataclass
class NewsItem:
    title: str
    published: datetime | None = None
    link: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "published": self.published.isoformat() if self.published else None, "link": self.link}


@dataclass
class NewsReport:
    score: int = 0
    critical: bool = False
    headline: str = ""
    blocked: bool = False
    items: list[NewsItem] = field(default_factory=list)
    by_symbol: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "critical": self.critical,
            "headline": self.headline,
            "blocked": self.blocked,
            "items": [i.to_dict() for i in self.items],
            "by_symbol": self.by_symbol,
        }


class NewsEngine:
    """Новостной риск-скоринг с фолбэком на офлайн."""

    def __init__(self, feeds: tuple[str, ...] = FEEDS, timeout: float = 4.0):
        self.feeds = feeds
        self.timeout = timeout

    # ----------------------------------------------------------- public
    def assess(
        self,
        symbol: str,
        *,
        upcoming_events: list[dict] | None = None,
        headlines: list[str] | None = None,
        items: list[NewsItem] | None = None,
        minutes_horizon: int = FRESHNESS_MIN,
    ) -> NewsReport:
        score = 0
        headline = ""
        by_symbol: dict[str, int] = {}
        found: list[NewsItem] = list(items or [])

        # Свежие заголовки из сети (тянем один раз на все символы — кэша
        # нет, но вызовы редкие: раз в торговый цикл).
        if items is None:
            found = self.fetch_recent(minutes_horizon)

        coin = symbol.split("/")[0].upper()
        coin_lower = coin.lower()

        for item in found:
            t = item.title or ""
            low = t.lower()
            weight = 0
            for kw in HIGH_IMPACT:
                if kw in low:
                    weight = max(weight, 45)
                    if not headline:
                        headline = t
                    break
            if weight == 0:
                for kw in MEDIUM_IMPACT:
                    if kw in low:
                        weight = max(weight, 15)
                        break
            if weight == 0:
                for kw in NEGATIVE_HINTS:
                    if kw in low:
                        weight = max(weight, 10)
                        break

            # Упоминание нашей монеты в новости (по тикеру или названию).
            mentions_coin = _coin_mentioned(low, coin_lower)
            # Новость про ДРУГУЮ монету (exploit какой-то альткойн) —
            # не должна блокировать BTC/ETH.
            other_coin = _mentions_other_coin(low, coin_lower)

            if mentions_coin and weight > 0:
                # Новость прямо про наш инструмент — усиливаем.
                weight = min(100, weight + 20)
                by_symbol[coin] = by_symbol.get(coin, 0) + weight
            elif weight >= 45 and other_coin and not mentions_coin:
                # Высокий импакт, но про чужую монету — для нашего
                # инструмента это лишь лёгкий фон, не блокер.
                weight = 10

            score += weight

        # События из календаря (если передали снаружи).
        for ev in upcoming_events or []:
            impact = str(ev.get("impact", "")).lower()
            if impact == "high":
                score += 40
                if not headline:
                    headline = ev.get("title", "High-impact event")
            elif impact == "medium":
                score += 15

        # Переданные заголовки (явный ввод из тестов/команды).
        for h in headlines or []:
            low = h.lower()
            if any(kw in low for kw in HIGH_IMPACT):
                score += 30

        score = min(100, score)
        return NewsReport(
            score=score,
            critical=score >= 60,
            blocked=score >= 60,
            headline=headline,
            items=found[:10],
            by_symbol=by_symbol,
        )

    # ----------------------------------------------------------- fetching
    def fetch_recent(self, minutes_horizon: int = FRESHNESS_MIN) -> list[NewsItem]:
        cutoff = datetime.now(UTC) - timedelta(minutes=minutes_horizon)
        out: list[NewsItem] = []
        for url in self.feeds:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (compatible; AstraBot/1.0)"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    xml_data = resp.read()
                out.extend(self._parse_rss(xml_data, cutoff))
                if len(out) >= MAX_ITEMS:
                    break
            except Exception as exc:
                logger.debug("News feed %s unavailable: %s", url, exc)
        return out[:MAX_ITEMS]

    @staticmethod
    def _parse_rss(xml_data: bytes, cutoff: datetime) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            return items
        # RSS 2.0: rss/channel/item; Atom: feed/entry
        for node in root.iter():
            if not node.tag.endswith("item") and not node.tag.endswith("entry"):
                continue
            title_el = next((c for c in node if c.tag.endswith("title")), None)
            link_el = next((c for c in node if c.tag.endswith("link")), None)
            date_el = next((c for c in node if c.tag.endswith("pubDate") or c.tag.endswith("updated")), None)
            if title_el is None or not title_el.text:
                continue
            published = None
            if date_el is not None and date_el.text:
                published = _parse_date(date_el.text)
            if published and published < cutoff:
                continue
            link = ""
            if link_el is not None:
                link = link_el.text or (link_el.attrib.get("href") if hasattr(link_el, "attrib") else "")
            items.append(NewsItem(title=title_el.text.strip(), published=published, link=link or ""))
        return items


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            continue
    return None
