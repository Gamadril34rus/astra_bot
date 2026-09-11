"""Telegram send rate-limit (TZ P2.6)."""

from __future__ import annotations

from astra_bot.telegram.rate_limit import TelegramRateLimiter


def test_burst_then_block():
    limiter = TelegramRateLimiter(max_per_minute=20, burst=3)
    assert limiter.allow()
    assert limiter.allow()
    assert limiter.allow()
    assert limiter.allow() is False


def test_critical_has_separate_cap():
    limiter = TelegramRateLimiter(max_per_minute=1, burst=1, critical_per_minute=2)
    assert limiter.allow() is True
    assert limiter.allow() is False
    assert limiter.allow(critical=True) is True
    assert limiter.allow(critical=True) is True
    assert limiter.allow(critical=True) is False
