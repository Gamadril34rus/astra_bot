"""Telegram send rate-limit (TZ P2.6)."""

from __future__ import annotations

from astra_bot.telegram.rate_limit import TelegramRateLimiter


def test_burst_then_block():
    limiter = TelegramRateLimiter(max_per_minute=20, burst=3)
    assert limiter.allow()
    assert limiter.allow()
    assert limiter.allow()
    assert limiter.allow() is False


def test_critical_bypasses_caps():
    limiter = TelegramRateLimiter(max_per_minute=2, burst=1, critical_per_minute=1)
    assert limiter.allow() is True
    regular = [limiter.allow() for _ in range(19)]
    assert all(ok is False for ok in regular)
    assert limiter.allow(critical=True) is True


def test_twenty_critical_never_dropped():
    limiter = TelegramRateLimiter(max_per_minute=1, burst=1, critical_per_minute=1)
    results = [limiter.allow(critical=True) for _ in range(20)]
    assert results == [True] * 20
