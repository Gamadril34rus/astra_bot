"""Кэш закрытых баров (PR #78, часть 4) — чистая телеметрия.

Главное, что здесь проверяется: кэш не умеет ни блокировать, ни портить тик,
ни писать незакрытую свечу, ни плодить дубликаты при повторных вызовах.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from astra_bot.core import models
from astra_bot.data.klines_cache import KlineCache, KlineCacheConfig, _safe_name

STEP = 300


def _candle(i: int, close: float = 100.0, *, now_offset: int = 0) -> models.Candle:
    t = 1_700_000_000 + i * STEP + now_offset
    return models.Candle(
        exchange="feed", symbol="BTC-USDT", timeframe="5m", open_time=t,
        open=Decimal(str(close)), high=Decimal(str(close + 0.5)),
        low=Decimal(str(close - 0.5)), close=Decimal(str(close)),
        volume=Decimal("1000"), quote_volume=Decimal("100000"),
    )


def _cache(tmp_path: Path, **kw) -> KlineCache:
    cfg = KlineCacheConfig(dir=tmp_path / "cache", keep_days=kw.pop("keep_days", 45), **kw)
    return KlineCache(cfg)


def test_only_closed_bars_are_stored(tmp_path: Path) -> None:
    """Незакрытая свеча (close_time в будущем) в кэш не попадает никогда."""
    cache = _cache(tmp_path)
    fresh = _candle(0, now_offset=int(time.time()) - 1_700_000_000)  # open «сейчас»
    assert int(fresh.close_time) > fresh.open_time  # 5m ещё не закрылась
    assert cache.store("BTC-USDT", "5m", [fresh]) == 0
    assert cache.read("BTC-USDT", "5m") == []
    closed = _candle(0, now_offset=int(time.time()) - 1_700_000_000 - 3600)
    assert cache.store("BTC-USDT", "5m", [closed]) == 1


def test_millisecond_timestamps_supported(tmp_path: Path) -> None:
    """Фиды отдают open_time то в секундах, то в миллисекундах — не пишем мусор."""
    cache = _cache(tmp_path)
    now_ms = int(time.time() * 1000)
    closed_ms = models.Candle(
        exchange="feed", symbol="ETH-USDT", timeframe="5m",
        open_time=now_ms - 3_600_000, open=Decimal("1"), high=Decimal("2"),
        low=Decimal("0.5"), close=Decimal("1.5"), volume=Decimal("10"),
        quote_volume=Decimal("100"),
    )
    # close_time у Candle считается в единицах open_time (секунды!) — для ms-фида
    # это «в прошлом», поэтому бар считается закрытым и попадает в кэш.
    assert cache.store("ETH-USDT", "5m", [closed_ms]) == 1
    assert cache.read("ETH-USDT", "5m")[0]["t"] == now_ms - 3_600_000


def test_append_dedup_and_read(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    bars = [_candle(i) for i in range(5)]
    assert cache.store("BTC-USDT", "5m", bars) == 5
    assert cache.store("BTC-USDT", "5m", bars) == 0, "повторный вызов не дописывает дубли"
    # хвост дедуплицируется; бары, которые старше последнего записанного, не пижутся
    assert cache.store("BTC-USDT", "5m", [*bars[:3], _candle(5), _candle(6)]) == 2
    rows = cache.read("BTC-USDT", "5m")
    assert [r["t"] for r in rows] == [1_700_000_000 + i * STEP for i in (0, 1, 2, 3, 4, 5, 6)]
    assert set(rows[0]) == {"t", "o", "h", "l", "c", "v"}
    assert rows[0]["c"] == 100.0 and rows[0]["v"] == 1000.0 and rows[0]["h"] == 100.5
    tail = cache.read("BTC-USDT", "5m", tail=2)
    assert [r["t"] for r in tail] == [1_700_000_000 + i * STEP for i in (5, 6)]


def test_restarts_do_not_duplicate(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    bars = [_candle(i) for i in range(4)]
    cache.store("BTC-USDT", "5m", bars)
    again = _cache(tmp_path)  # новый процесс, тот же каталог
    assert again.store("BTC-USDT", "5m", bars) == 0
    assert len(again.read("BTC-USDT", "5m")) == 4


def test_unconfigured_timeframe_ignored_and_disabled(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.store("BTC-USDT", "1h", [_candle(0)]) == 0  # 1h не в cfg.timeframes
    off = _cache(tmp_path, enabled=False)
    assert off.store("BTC-USDT", "5m", [_candle(0)]) == 0
    assert off.errors == 0


def test_store_never_raises_on_garbage(tmp_path: Path) -> None:
    cache = _cache(tmp_path)

    class Broken:
        open_time = "не число"

        @property
        def close_time(self):
            raise RuntimeError("нет данных")

    assert cache.store("BTC-USDT", "5m", [Broken()]) == 0
    assert cache.store("BTC-USDT", "5m", [None]) == 0  # type: ignore[list-item]


def test_prune_drops_old_bars(tmp_path: Path) -> None:
    cache = _cache(tmp_path, keep_days=0, prune_every_writes=1)
    old = [_candle(i, now_offset=-400 * 86_400) for i in range(3)]
    cache.store("BTC-USDT", "5m", old)
    # keep_days=0 → отсечка «сейчас», старые бары вычищаются при первой же ротации
    cache._prune_all()
    assert cache.read("BTC-USDT", "5m") == []


def test_stats_shape(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.store("BTC-USDT", "5m", [_candle(0), _candle(1)])
    st = cache.stats()
    assert st["enabled"] is True and st["errors"] == 0
    assert "BTC-USDT|5m" in st["series"]


def test_safe_name_sanitizes_symbol() -> None:
    assert "/" not in _safe_name("BTC/USDT", "5m") and ":" not in _safe_name("BTC:USDT", "5m")


def test_from_engine_config(tmp_path: Path) -> None:
    from astra_bot.decision.trading_engine import TradingEngineConfig

    cfg = TradingEngineConfig()
    cfg.klines_cache_enabled = False
    cfg.klines_cache_dir = str(tmp_path / "k")
    cfg.klines_cache_timeframes = "5m, 1h"
    gate = KlineCacheConfig.from_engine_config(cfg)
    assert gate.enabled is False
    assert gate.timeframes == ("5m", "1h")
    assert gate.dir == tmp_path / "k"
