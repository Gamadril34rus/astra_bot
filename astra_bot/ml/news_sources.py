"""Бесплатные новостные источники для ASTRA BOT.

Источники:

* **GDELT DOC 2.0** — основной источник. Не требует API-ключа. Даёт текущие
  новости, таймлайн тональности (``timelinetone``) и список статей
  (``artlist``). DOC API хранит архив примерно за последние 3 месяца, поэтому
  для исторического обогащения мы запрашиваем помесячные окна и аккуратно
  возвращаем пустой результат, когда окно выходит за пределы архива (GDELT
  отвечает ошибкой/пустым телом — это нормальный ожидаемый фолбэк, а не
  сбой).
* **Free Crypto News API** (cryptocurrency.cv) — дополнительный источник без
  API-ключа. Агрегирует крипто-заголовки. Используется как дополнение к
  GDELT: его ответ усиливает/уточняет сентимент. Любой сбой (сеть, таймаут,
  неожиданный формат, rate-limit) проглатывается и не влияет на основной
  поток — вызывающий код всегда получает либо данные GDELT, либо безопасный
  нейтральный снапшот.

Никаких платных ключей (``NEWS_API_KEY`` и т.п.) этот модуль не использует.
Денежные/боевые ордера к новостям не подключаются — модуль только отдаёт
признаки сентимента/объёма для research/paper-торговли.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# GDELT DOC 2.0 — бесплатно, без ключа.
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# Free Crypto News API — бесплатно, без ключа (https://cryptocurrency.cv).
FREE_CRYPTO_NEWS_URL = "https://cryptocurrency.cv/api/news"

# GDELT официально отдаёт ~3 месяца архива через DOC API. Используем это как
# мягкий предел: для более старых месяцев помесячный запрос просто вернёт
# пусто и помечается как ``out_of_archive``.
GDELT_MAX_ARCHIVE_DAYS = 92

# Чувствительные к регистру/языку слова для локального скоринга текста, когда
# у источника нет собственного поля тональности (Free Crypto News API).
POSITIVE = {
    "approval", "approved", "adoption", "launch", "launched", "partnership",
    "upgrade", "growth", "surge", "bullish", "buy", "inflow", "record", "rally",
    "etf", "institutional", "integration", "listing", "profit", "breakout",
    "all-time high", "ath", "milestone", "accumulate", "recovery", "soars",
}
NEGATIVE = {
    "hack", "hacked", "exploit", "lawsuit", "ban", "banned", "fraud", "scam",
    "bearish", "sell", "outflow", "crash", "collapse", "liquidation", "delist",
    "delisted", "fine", "sanction", "attack", "bankruptcy", "panic", "rug",
    "plunge", "plunges", "tumble", "slump", "dump", "dumps", "breach",
    "sec charges", "sec sues", "outage", "depeg",
}

# Чанк размера окна GDELT. DOC API надёжно отдаёт до 3 месяцев; запрашиваем
# помесячно, чтобы не упираться в лимиты и не терять статьи на окнах, где
# часть периода выходит за пределы архива.
HISTORICAL_WINDOW_DAYS = 30


@dataclass(frozen=True)
class SourceArticle:
    """Нормализованная статья из любого источника."""

    title: str
    description: str = ""
    url: str = ""
    source: str = ""
    published: datetime | None = None
    # Собственный тон источника в диапазоне [-1, 1], если известен (GDELT
    # artlist отдаёт ``tone`` в долях). None — считаем локально.
    tone: float | None = None

    @property
    def text(self) -> str:
        return f"{self.title} {self.description}".strip()


@dataclass(frozen=True)
class SourceResult:
    """Результат опроса одного источника."""

    articles: list[SourceArticle]
    source: str
    # Собственный усреднённый тон источника в [-1, 1], если отдаётся
    # агрегированно (например, GDELT timelinetone).
    aggregate_tone: float | None = None
    ok: bool = True
    note: str = ""


def score_text(text: str) -> float:
    """Простой прозрачный лексический сентимент в диапазоне [-1, 1]."""
    words = set(re.findall(r"[a-zA-Z][a-zA-Z.-]{2,}", text.lower()))
    pos = sum(1 for w in words if w in POSITIVE)
    neg = sum(1 for w in words if w in NEGATIVE)
    total = pos + neg
    return 0.0 if total == 0 else (pos - neg) / total


def gdelt_asset_query(symbol: str, aliases: dict[str, tuple[str, ...]] | None = None) -> str:
    """Построить булев запрос GDELT для тикера/алиасов актива."""
    asset = symbol.split("/")[0].upper()
    table = aliases or _DEFAULT_ALIASES
    names = table.get(asset, (asset.lower(),))
    # GDELT не любит слишком длинные OR-цепочки; берём три самых частых
    # варианта написания актива.
    terms = [f'"{name}"' for name in names[:3]]
    return " OR ".join(terms)


# Дефолтные алиасы вынесены в модуль, чтобы не дублировать словарь между
# news_features и источниками. Импортируется отсюда.
_DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
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

ASSET_ALIASES = _DEFAULT_ALIASES


def _fmt_gdelt_dt(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def _parse_gdelt_seen(value: Any) -> datetime | None:
    """GDELT ``seendate`` имеет формат YYYYMMDDTHHMMSSZ."""
    if not isinstance(value, str) or len(value) < 8:
        return None
    raw = value.replace("T", "").replace("Z", "")
    try:
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


class NewsSources:
    """Асинхронный клиент бесплатных новостных источников.

    Методы никогда не поднимают исключений наружу: сеть/источник считаются
    ненадёжными, и любой сбой превращается в ``SourceResult(ok=False, ...)``.
    Задача вызывающего кода — решить, как агрегировать результаты и какой
    фолбэк использовать.
    """

    def __init__(
        self,
        *,
        timeout: aiohttp.ClientTimeout | None = None,
        free_crypto_news_enabled: bool = True,
        user_agent: str = "Mozilla/5.0 (compatible; AstraBot/1.0; +paper-only)",
    ) -> None:
        self.timeout = timeout or aiohttp.ClientTimeout(total=20)
        self.free_crypto_news_enabled = free_crypto_news_enabled
        self.user_agent = user_agent

    # ------------------------------------------------------------------ GDELT
    async def gdelt_timeline(
        self,
        session: aiohttp.ClientSession,
        query: str,
        *,
        timespan: str = "1d",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> SourceResult:
        """Агрегированный тон GDELT (``timelinetone``).

        Используется для текущего снапшота: одна лёгкая JSON-выдача с
        почасовым тоном за указанный ``timespan``.
        """
        params: dict[str, str] = {
            "query": query,
            "mode": "timelinetone",
            "format": "json",
            "maxrecords": "250",
        }
        if start is not None and end is not None:
            params["startdatetime"] = _fmt_gdelt_dt(start)
            params["enddatetime"] = _fmt_gdelt_dt(end)
        else:
            params["timespan"] = timespan

        try:
            async with session.get(
                GDELT_DOC_URL, params=params, headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    return SourceResult([], "gdelt", ok=False, note=f"http_{resp.status}")
                # GDELT иногда отдаёт text/javascript вместо application/json.
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("GDELT timeline unavailable: %s", exc)
            return SourceResult([], "gdelt", ok=False, note=f"error:{type(exc).__name__}")

        timeline = data.get("timeline") or []
        points = [p for p in timeline if isinstance(p, dict)]
        if not points:
            return SourceResult([], "gdelt", ok=True, note="empty")

        # Каждая точка: {"date": ..., "tone": "-2.34", ...}.
        tones: list[float] = []
        for point in points:
            raw = point.get("tone")
            try:
                tones.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not tones:
            return SourceResult([], "gdelt", ok=True, note="no_tone")

        avg = sum(tones) / len(tones)
        return SourceResult([], "gdelt", aggregate_tone=max(-1.0, min(1.0, avg / 10.0)))

    async def gdelt_articles(
        self,
        session: aiohttp.ClientSession,
        query: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        maxrecords: int = 75,
    ) -> SourceResult:
        """Список статей GDELT (``artlist``) с полем ``tone``."""
        params: dict[str, str] = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(min(maxrecords, 250)),
            "sort": "datedesc",
        }
        if start is not None and end is not None:
            params["startdatetime"] = _fmt_gdelt_dt(start)
            params["enddatetime"] = _fmt_gdelt_dt(end)
        else:
            params["timespan"] = "1d"

        try:
            async with session.get(
                GDELT_DOC_URL, params=params, headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    return SourceResult([], "gdelt", ok=False, note=f"http_{resp.status}")
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("GDELT artlist unavailable: %s", exc)
            return SourceResult([], "gdelt", ok=False, note=f"error:{type(exc).__name__}")

        raw_articles = data.get("articles") or []
        articles: list[SourceArticle] = []
        for row in raw_articles:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if not title:
                continue
            tone_raw = row.get("tone")
            try:
                tone = float(tone_raw) / 10.0 if tone_raw not in (None, "") else None
                if tone is not None:
                    tone = max(-1.0, min(1.0, tone))
            except (TypeError, ValueError):
                tone = None
            articles.append(
                SourceArticle(
                    title=title,
                    description=(row.get("seendate") or ""),
                    url=row.get("url", "") or "",
                    source=row.get("domain", "") or "gdelt",
                    published=_parse_gdelt_seen(row.get("seendate")),
                    tone=tone,
                )
            )
        return SourceResult(articles, "gdelt")

    # ----------------------------------------------------- Free Crypto News
    async def free_crypto_news(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        *,
        limit: int = 50,
    ) -> SourceResult:
        """Дополнительный источник: Free Crypto News API (cryptocurrency.cv).

        Эндпоинт публичный, без ключа. Умеет фильтровать по тикеру
        (``?ticker=BTC``). Любой сбой изолирован: метод возвращает
        ``ok=False`` и никогда не бросает исключение.
        """
        if not self.free_crypto_news_enabled:
            return SourceResult([], "free_crypto_news", ok=False, note="disabled")

        asset = symbol.split("/")[0].upper()
        params = {"limit": str(min(max(limit, 1), 100)), "ticker": asset}
        try:
            async with session.get(
                FREE_CRYPTO_NEWS_URL, params=params, headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    return SourceResult(
                        [], "free_crypto_news", ok=False, note=f"http_{resp.status}"
                    )
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("Free Crypto News API unavailable: %s", exc)
            return SourceResult(
                [], "free_crypto_news", ok=False, note=f"error:{type(exc).__name__}"
            )

        if not isinstance(data, dict):
            return SourceResult([], "free_crypto_news", ok=False, note="bad_format")

        rows = data.get("articles")
        if not isinstance(rows, list):
            # Иногда эндпоинт отдаёт {"data": [...]} или похожие варианты.
            rows = data.get("data") if isinstance(data.get("data"), list) else []
        articles: list[SourceArticle] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            if not title:
                continue
            published = _parse_iso_date(row.get("pubDate") or row.get("publishedAt"))
            articles.append(
                SourceArticle(
                    title=title,
                    description=(row.get("description") or "").strip(),
                    url=row.get("link") or row.get("url") or "",
                    source=row.get("source") or "cryptocurrency.cv",
                    published=published,
                )
            )
        return SourceResult(articles, "free_crypto_news")

    # ------------------------------------------------------------- helpers
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json,text/javascript,*/*",
        }


def _parse_iso_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def aggregate_results(
    primary: SourceResult,
    *secondary: SourceResult,
    asset_query: str = "",
) -> tuple[float, float, float, float, str, int]:
    """Слить результаты источников в единый признаковый набор.

    Возвращает кортеж ``(sentiment, volume, shock, confidence, source,
    articles_count)`` с безопасными диапазонами:

    * sentiment/shock ∈ [-1, 1];
    * volume/confidence ∈ [0, 1];
    * ``source`` — перечисление реально сработавших источников через ``+``.

    Слияние взвешенное: собственный тон GDELT (если есть) имеет приоритет,
    статьи Free Crypto News уточняют его. Ни один источник не может
    единолично вывести сентимент за пределы допустимого при пустом основном.
    """
    tones: list[float] = []
    article_tones: list[float] = []
    sources_used: list[str] = []
    total_articles = 0

    if primary.ok:
        if primary.aggregate_tone is not None:
            tones.append(primary.aggregate_tone)
        for art in primary.articles:
            if art.tone is not None:
                article_tones.append(art.tone)
            elif art.text:
                article_tones.append(score_text(art.text))
        total_articles += len(primary.articles)
        if primary.aggregate_tone is not None or primary.articles:
            sources_used.append(primary.source)

    for result in secondary:
        if not result.ok:
            continue
        for art in result.articles:
            if art.text:
                article_tones.append(score_text(art.text))
        total_articles += len(result.articles)
        if result.articles:
            sources_used.append(result.source)

    if not tones and article_tones:
        tones = article_tones

    if not tones:
        # Ни один источник не дал тона — безопасный нейтральный снапшот.
        return 0.0, 0.0, 0.0, 0.0, "+".join(sources_used) or "none", total_articles

    # Средний сентимент; статьи Free Crypto News входят с пониженным весом,
    # чтобы первичный GDELT-тон доминировал.
    if primary.aggregate_tone is not None:
        # 70% — агрегированный тон GDELT, 30% — статьи (обоих источников).
        article_avg = sum(article_tones) / len(article_tones) if article_tones else primary.aggregate_tone
        sentiment = 0.7 * primary.aggregate_tone + 0.3 * article_avg
    else:
        sentiment = sum(tones) / len(tones)

    sentiment = max(-1.0, min(1.0, sentiment))
    volume = min(1.0, total_articles / 75.0) if total_articles else min(1.0, len(tones) / 24.0)
    shock = sentiment * min(1.0, (total_articles or len(tones)) / 25.0)
    confidence = min(1.0, (total_articles or len(tones)) / 20.0)
    source_label = "+".join(sources_used) if sources_used else "none"
    return sentiment, volume, shock, confidence, source_label, total_articles


def historical_window_supported(start: datetime, end: datetime) -> bool:
    """Грубо проверяет, попадает ли окно в 3-месячный архив GDELT DOC.

    DOC API может отдать данные старше при наличии, но официально не
    гарантирует. Используем как эвристику для логирования/маркировки.
    """
    now = datetime.now(tz=UTC)
    return (now - start.astimezone(UTC)).days <= GDELT_MAX_ARCHIVE_DAYS
