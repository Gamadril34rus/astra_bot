"""Exit Manager: MAX_HOLD / VOL_EXPANSION / BTC_PANIC с причинами (Этап 4)."""

from __future__ import annotations

import asyncio
import random
import time
from decimal import Decimal
from pathlib import Path

from astra_bot.core import models
from astra_bot.decision.broker import PaperBroker, PaperPosition
from astra_bot.decision.exit_manager import ExitManager
from tests.integration.test_main_tick import make_bot
from tests.integration.test_meta_strategy_execution import FeedStub, gen_candles

SYMBOL = "BTC-USDT"


def _candles(n: int = 90, price: float = 100.0, rng: float = 0.5, seed: int = 7,
             symbol: str = SYMBOL, timeframe: str = "1h") -> list[models.Candle]:
    """Стабильные свечи: диапазон бара ≈ rng."""
    rnd = random.Random(seed)
    t0 = 1_700_000_000
    out = []
    for i in range(n):
        o = price + rnd.uniform(-rng, rng)
        c = o + rnd.uniform(-rng, rng)
        h = max(o, c) + rnd.uniform(0, rng * 0.5)
        lo = min(o, c) - rnd.uniform(0, rng * 0.5)
        out.append(
            models.Candle(
                exchange="feed", symbol=symbol, timeframe=timeframe,
                open_time=t0 + i * 3600,
                open=Decimal(str(round(o, 8))), high=Decimal(str(round(h, 8))),
                low=Decimal(str(round(lo, 8))), close=Decimal(str(round(c, 8))),
                volume=Decimal("10"), quote_volume=Decimal("1000"),
            )
        )
    return out


def _with_spike(candles: list[models.Candle], rng: float = 4.0) -> list[models.Candle]:
    """Добавить бар с диапазоном ×8 (скачок волатильности)."""
    last = candles[-1]
    mid = float(last.close)
    spike = models.Candle(
        exchange="feed", symbol=SYMBOL, timeframe=candles[-1].timeframe,
        open_time=last.open_time + 3600,
        open=Decimal(str(mid)), high=Decimal(str(mid + rng)),
        low=Decimal(str(mid - rng)), close=Decimal(str(mid)),
        volume=Decimal("10"), quote_volume=Decimal("1000"),
    )
    return [*candles, spike]


def _crash(n: int = 30, price: float = 100.0, drop: float = 0.08) -> list[models.Candle]:
    """30 «4h»-свечей: плато, последний бар −8% (BTC-обвал)."""
    out = _candles(n - 1, price=price, rng=0.5, timeframe="4h")
    last = out[-1]
    mid = float(last.close)
    crash = models.Candle(
        exchange="feed", symbol=SYMBOL, timeframe="4h",
        open_time=last.open_time + 14400,
        open=Decimal(str(mid)), high=Decimal(str(mid * 1.001)),
        low=Decimal(str(mid * (1 - drop) * 0.999)),
        close=Decimal(str(mid * (1 - drop))),
        volume=Decimal("10"), quote_volume=Decimal("1000"),
    )
    return [*out, crash]


def _mk_broker(tmp_path: Path) -> PaperBroker:
    return PaperBroker(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        initial_capital=Decimal("1000"),
        fee_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
    )


def _mk_pos(broker: PaperBroker, **kw) -> PaperPosition:
    base = dict(
        id=kw.pop("id", "pos-1"),
        symbol=SYMBOL,
        direction=kw.pop("direction", "long"),
        entry_price=Decimal("100"),
        quantity=Decimal("1"),
        stop_loss=Decimal("98"),
        take_profits=[Decimal("110")],
        tp_filled=[False],
        initial_quantity=Decimal("1"),
        risk_distance=Decimal("2"),
        regime="TREND",
        timeframe="1h",
    )
    base.update(kw)
    pos = PaperPosition(**base)
    broker.positions.append(pos)
    return pos


class TestMaxHold:
    def test_position_held_too_long_is_closed(self, tmp_path):
        broker = _mk_broker(tmp_path)
        pos = _mk_pos(broker, opened_at=int(time.time() * 1000) - 49 * 3600 * 1000)
        em = ExitManager(exchange=None, broker=broker)
        closed = em.check_symbol([pos], {"1h": _candles()}, 100.0)
        assert len(closed) == 1
        assert closed[0].exit_reason == "MAX_HOLD"
        assert broker.positions == []

    def test_fresh_position_kept(self, tmp_path):
        broker = _mk_broker(tmp_path)
        pos = _mk_pos(broker, opened_at=int(time.time() * 1000))
        em = ExitManager(exchange=None, broker=broker)
        assert em.check_symbol([pos], {"1h": _candles()}, 100.0) == []
        assert broker.positions == [pos]


class TestVolExpansion:
    def test_spike_closes_position(self, tmp_path):
        broker = _mk_broker(tmp_path)
        pos = _mk_pos(broker)
        em = ExitManager(exchange=None, broker=broker)
        candles = _with_spike(_candles())
        closed = em.check_symbol([pos], {"1h": candles}, 100.0)
        assert len(closed) == 1
        assert closed[0].exit_reason == "VOL_EXPANSION"

    def test_stable_vol_kept(self, tmp_path):
        broker = _mk_broker(tmp_path)
        pos = _mk_pos(broker)
        em = ExitManager(exchange=None, broker=broker)
        assert em.check_symbol([pos], {"1h": _candles()}, 100.0) == []

    def test_no_candles_no_close(self, tmp_path):
        """Недостаточно баров → решение не принимается (не выходим вслепую)."""
        broker = _mk_broker(tmp_path)
        pos = _mk_pos(broker)
        em = ExitManager(exchange=None, broker=broker)
        assert em.check_symbol([pos], {"1h": _candles(20)}, 100.0) == []


class TestBtcPanic:
    def test_panic_flag_set_on_crash(self):
        em = ExitManager(exchange=FeedStub(_crash()), broker=None)
        assert asyncio.run(em.refresh_btc_panic()) is True

    def test_no_panic_on_flat_market(self):
        em = ExitManager(exchange=FeedStub(_candles(30, timeframe="4h")), broker=None)
        assert asyncio.run(em.refresh_btc_panic()) is False

    def test_api_error_keeps_last_flag(self):
        class Broken:
            async def get_candles(self, *a, **k):
                raise RuntimeError("no network")

        em = ExitManager(exchange=Broken(), broker=None)
        em.btc_panic = True  # был обвал, теперь сеть недоступна
        assert asyncio.run(em.refresh_btc_panic()) is True

    def test_flatten_closes_all_with_reason(self, tmp_path):
        broker = _mk_broker(tmp_path)
        _mk_pos(broker, id="p1")
        _mk_pos(broker, id="p2", direction="short")
        em = ExitManager(exchange=None, broker=broker)
        closed = em.flatten_symbol(SYMBOL, 100.0)
        assert {t.id for t in closed} == {"p1", "p2"}
        assert all(t.exit_reason == "BTC_PANIC" for t in closed)
        assert broker.positions == []


class TestEngineIntegration:
    def test_btc_panic_flattens_and_lessons(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        assert len(eng.broker.positions) == 1
        opened = [p.id for p in eng.broker.positions]

        # Обвал BTC (стуб возвращает серию краха для любого запроса).
        eng.exchange.candles = _crash()
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())

        assert eng.broker.positions == []
        panic_lessons = [les for les in lessons if les.get("id") in opened]
        assert panic_lessons, "lesson по panic-выходу обязателен"
        assert all(les.get("exit_reason") == "BTC_PANIC" for les in panic_lessons)

    def test_max_hold_via_engine(self, tmp_path, monkeypatch):
        lessons: list[dict] = []
        monkeypatch.setattr(
            "astra_bot.decision.trading_engine.append_lessons",
            lambda trades: lessons.extend(trades) or 1,
        )
        bot = make_bot(tmp_path, FeedStub(gen_candles()), monkeypatch)
        eng = bot._trading_engine
        asyncio.run(bot._tick())
        pos = eng.broker.positions[0]
        pos_id = pos.id
        # «Позиция живёт 49 часов» (в тестах время фальшивим).
        pos.opened_at = int(time.time() * 1000) - 49 * 3600 * 1000
        bot._last_tick_at = 0.0
        asyncio.run(bot._tick())
        hold_lessons = [les for les in lessons if les.get("id") == pos_id]
        assert hold_lessons
        assert hold_lessons[0].get("exit_reason") == "MAX_HOLD"
