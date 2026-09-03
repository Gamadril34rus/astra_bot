"""Тесты бесплатных новостных источников (GDELT + Free Crypto News API).

Сеть в unit-тестах не дёргается: используются подставные ответы через
``aiohttp``-заглушки на уровне ``ClientSession._request``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from astra_bot.ml.news_features import NewsFeatureService, NewsSnapshot
from astra_bot.ml.news_sources import (
    ASSET_ALIASES,
    NewsSources,
    SourceArticle,
    SourceResult,
    aggregate_results,
    gdelt_asset_query,
    historical_window_supported,
    score_text,
)


# --------------------------------------------------------------------- helpers
class _FakeResponse:
    def __init__(self, status: int, payload: Any, ctype: str = "application/json") -> None:
        self.status = status
        self._payload = payload
        self.content_type = ctype

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type: str | None = None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """Подставляет заранее подготовленные ответы по URL-префиксу."""

    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self._routes = routes
        self.calls: list[dict] = []
        self.closed = False

    def _match(self, url: str) -> _FakeResponse:
        for prefix, resp in self._routes.items():
            if url.startswith(prefix):
                return resp
        return _FakeResponse(404, {"error": "no route"})

    def get(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        return self._match(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        return False


def _run(coro):
    """Прогнать корутину в свежем event loop.

    ``asyncio.get_event_loop()`` здесь использовать нельзя: после async-тестов
    pytest-asyncio (auto-режим) закрывает свой loop, и get_event_loop() в
    главном потоке начинает бросать RuntimeError, из-за чего эти тесты падали
    в общем прогоне в зависимости от порядка файлов.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------- pure
def test_score_text_balances_positive_and_negative():
    assert score_text("Bitcoin bullish rally and adoption record") > 0
    assert score_text("Exchange hacked, crash and liquidation panic") < 0
    assert score_text("nothing of note here") == 0.0


def test_asset_query_uses_aliases():
    q = gdelt_asset_query("BTC/USDT")
    assert '"bitcoin"' in q
    assert '"btc"' in q
    assert " OR " in q


def test_aliases_cover_universe_tickers():
    # Основные мажоры, на которых работает paper-торговля.
    for asset in ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX"):
        assert asset in ASSET_ALIASES
        assert ASSET_ALIASES[asset]


def test_historical_window_supported_flags_old_windows():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(tz=UTC)
    assert historical_window_supported(now - timedelta(days=10), now) is True
    assert historical_window_supported(now - timedelta(days=200), now - timedelta(days=180)) is False


# --------------------------------------------------------------- GDELT parsing
def test_gdelt_timeline_extracts_average_tone():
    payload = {
        "timeline": [
            {"date": "20240101000000", "tone": "-5.0"},
            {"date": "20240101010000", "tone": "3.0"},
            {"date": "20240101020000", "tone": "1.0"},
        ]
    }
    session = _FakeSession(
        {"https://api.gdeltproject.org/api/v2/doc/doc": _FakeResponse(200, payload)}
    )
    sources = NewsSources()
    res = _run(sources.gdelt_timeline(session, "bitcoin"))
    assert res.ok is True
    assert res.source == "gdelt"
    # (-5 + 3 + 1)/3 = -0.333, делено на 10 -> -0.0333
    assert res.aggregate_tone == pytest.approx(-0.033333, abs=1e-4)


def test_gdelt_timeline_safe_on_http_error():
    session = _FakeSession(
        {"https://api.gdeltproject.org/api/v2/doc/doc": _FakeResponse(503, {})}
    )
    sources = NewsSources()
    res = _run(sources.gdelt_timeline(session, "bitcoin"))
    assert res.ok is False
    assert "503" in res.note
    assert res.aggregate_tone is None


def test_gdelt_articles_parse_tone_and_url():
    payload = {
        "articles": [
            {
                "title": "Bitcoin ETF approved",
                "url": "https://example.com/a",
                "domain": "example.com",
                "seendate": "20240110T120000Z",
                "tone": "4.2",
            },
            {"title": "", "url": "https://example.com/skip"},
            {
                "title": "Exchange hacked",
                "url": "https://example.com/b",
                "domain": "example.com",
                "seendate": "20240110T130000Z",
                "tone": "-6.0",
            },
        ]
    }
    session = _FakeSession(
        {"https://api.gdeltproject.org/api/v2/doc/doc": _FakeResponse(200, payload)}
    )
    sources = NewsSources()
    res = _run(sources.gdelt_articles(session, "bitcoin"))
    assert res.ok is True
    assert len(res.articles) == 2
    assert res.articles[0].title == "Bitcoin ETF approved"
    assert res.articles[0].tone == pytest.approx(0.42, abs=1e-6)
    assert res.articles[1].published is not None


# ----------------------------------------------------- Free Crypto News parsing
def test_free_crypto_news_parses_standard_shape():
    payload = {
        "articles": [
            {
                "title": "Bitcoin rallies to new high",
                "description": "ETF inflows surge",
                "link": "https://example.com/1",
                "source": "CoinDesk",
                "pubDate": "2024-01-10T12:00:00Z",
            },
            {
                "title": "Major protocol exploit",
                "description": "funds at risk",
                "link": "https://example.com/2",
                "source": "CoinTelegraph",
                "pubDate": "2024-01-10T13:00:00Z",
            },
        ],
        "totalCount": 2,
    }
    session = _FakeSession(
        {"https://cryptocurrency.cv/api/news": _FakeResponse(200, payload)}
    )
    sources = NewsSources()
    res = _run(sources.free_crypto_news(session, "BTC/USDT"))
    assert res.ok is True
    assert res.source == "free_crypto_news"
    assert len(res.articles) == 2
    assert res.articles[0].published is not None
    # ticker=BTC должен попасть в запрос (aiohttp передаёт params отдельно).
    assert session.calls
    assert session.calls[0]["kwargs"].get("params", {}).get("ticker") == "BTC"


def test_free_crypto_news_safe_on_failure():
    session = _FakeSession(
        {"https://cryptocurrency.cv/api/news": _FakeResponse(500, {})}
    )
    sources = NewsSources()
    res = _run(sources.free_crypto_news(session, "BTC/USDT"))
    assert res.ok is False
    assert "500" in res.note
    assert res.articles == []


def test_free_crypto_news_can_be_disabled():
    session = _FakeSession({})
    sources = NewsSources(free_crypto_news_enabled=False)
    res = _run(sources.free_crypto_news(session, "BTC/USDT"))
    assert res.ok is False
    assert res.note == "disabled"
    assert session.calls == []


# --------------------------------------------------------------- aggregation
def test_aggregate_results_primary_dominates():
    primary = SourceResult([], "gdelt", aggregate_tone=0.4)
    secondary = SourceResult(
        [SourceArticle(title="bad hack scam crash", tone=-0.9)],
        "free_crypto_news",
    )
    sentiment, volume, _shock, confidence, source, articles = aggregate_results(
        primary, secondary
    )
    # Вторичный текст лексически негативен (все слова из NEGATIVE), поэтому
    # article_avg = -1.0; агрегат: 0.7*0.4 + 0.3*(-1.0) = -0.02.
    assert sentiment == pytest.approx(-0.02, abs=1e-6)
    assert "gdelt" in source
    assert "free_crypto_news" in source
    assert articles == 1
    assert 0.0 <= volume <= 1.0
    assert 0.0 <= confidence <= 1.0


def test_aggregate_results_safe_when_nothing_available():
    empty = SourceResult([], "gdelt", ok=False, note="http_503")
    sentiment, volume, _shock, confidence, source, articles = aggregate_results(
        empty
    )
    assert (sentiment, volume, _shock, confidence, articles) == (0.0, 0.0, 0.0, 0.0, 0)
    assert source == "none"


def test_aggregate_results_uses_article_tones_when_no_aggregate():
    primary = SourceResult(
        [
            SourceArticle(title="bullish rally adoption", tone=0.5),
            SourceArticle(title="record inflow", tone=0.3),
        ],
        "gdelt",
    )
    sentiment, _vol, _shock, _conf, source, articles = aggregate_results(primary)
    assert sentiment == pytest.approx(0.4)
    assert source == "gdelt"
    assert articles == 2


# ---------------------------------------------------------- NewsFeatureService
def test_service_uses_gdelt_plus_free_crypto_news(monkeypatch):
    """``current`` должен слить GDELT и Free Crypto News, не обращаясь к
    платным ключам."""
    gdelt_timeline = SourceResult([], "gdelt", aggregate_tone=0.25)
    gdelt_articles = SourceResult(
        [SourceArticle(title="Bitcoin etf inflow", tone=0.4)], "gdelt"
    )
    free = SourceResult(
        [SourceArticle(title="Partnership launch growth", tone=0.6)],
        "free_crypto_news",
    )

    sources = NewsSources()

    async def fake_timeline(*a, **k):
        return gdelt_timeline

    async def fake_articles(*a, **k):
        return gdelt_articles

    async def fake_free(*a, **k):
        return free

    monkeypatch.setattr(sources, "gdelt_timeline", fake_timeline)
    monkeypatch.setattr(sources, "gdelt_articles", fake_articles)
    monkeypatch.setattr(sources, "free_crypto_news", fake_free)

    # Перехватываем создание aiohttp-сессии, чтобы не лезть в сеть.
    class _CtxSession(_FakeSession):
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    import astra_bot.ml.news_features as nf

    monkeypatch.setattr(
        nf.aiohttp,
        "ClientSession",
        lambda *a, **k: _CtxSession({}),
    )

    svc = NewsFeatureService(sources=sources)
    snap = _run(svc.current("BTC/USDT"))
    assert snap.sentiment > 0
    assert "gdelt" in snap.source
    assert "free_crypto_news" in snap.source
    assert snap.articles >= 1
    # Платный ключ не используется.
    assert svc.news_api_key == ""
    assert "NEWS_API_KEY" not in snap.source


def test_service_falls_back_to_neutral_when_all_sources_fail(monkeypatch):
    sources = NewsSources()

    async def boom(*a, **k):
        return SourceResult([], "gdelt", ok=False, note="http_500")

    async def boom_free(*a, **k):
        return SourceResult([], "free_crypto_news", ok=False, note="http_500")

    monkeypatch.setattr(sources, "gdelt_timeline", boom)
    monkeypatch.setattr(sources, "gdelt_articles", boom)
    monkeypatch.setattr(sources, "free_crypto_news", boom_free)

    import astra_bot.ml.news_features as nf

    class _CtxSession(_FakeSession):
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        nf.aiohttp, "ClientSession", lambda *a, **k: _CtxSession({})
    )

    svc = NewsFeatureService(sources=sources)
    snap = _run(svc.current("BTC/USDT"))
    assert snap.sentiment == 0.0
    assert snap.confidence == 0.0
    assert snap.source == "fallback"


def test_service_historical_window_outside_archive_is_neutral(tmp_path):
    from datetime import UTC, datetime, timedelta

    svc = NewsFeatureService(tmp_path / "news_cache.json")
    old_start = datetime.now(tz=UTC) - timedelta(days=400)
    old_end = old_start + timedelta(days=30)
    snap = _run(svc.fetch_historical_window("BTC/USDT", old_start, old_end))
    assert snap.source == "gdelt-out-of-archive"
    assert snap.sentiment == 0.0


def test_cache_round_trip(tmp_path):
    cache = tmp_path / "news_cache.json"
    svc = NewsFeatureService(cache)
    svc.put_historical(
        "BTC/USDT",
        datetime(2024, 1, 15, tzinfo=UTC),
        NewsSnapshot(sentiment=0.3, volume=0.5, confidence=0.8, source="gdelt", articles=10),
    )
    svc.save_cache()

    loaded = NewsFeatureService(cache)
    when = int(datetime(2024, 1, 20, tzinfo=UTC).timestamp() * 1000)
    snap = loaded.cached_historical("BTC/USDT", when)
    assert snap.sentiment == pytest.approx(0.3)
    assert snap.articles == 10
