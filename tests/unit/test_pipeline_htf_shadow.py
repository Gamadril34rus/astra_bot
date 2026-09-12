"""D5 фаза 0: HTF shadow-фильтр (docs/HTF_DIRECTIONAL_FILTER_PLAN.md).

Проверяет свойства фазы 0:
  а) НЕ блокирует входы — решение пайплайна идентично с флагом и без,
     гипотетический запрет уходит только в журнал;
  б) флип-стратегии (список имён) исключены;
  в) fail-open при < 60 закрытых 4h-барах (и флаг выключен — тишина);
  плюс дедуп (один бар — одна запись на пару стратегия+сторона)
  и поля кандидата в записи (решение владельца 12.09).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from astra_bot.core import models
from astra_bot.decision import DecisionConfig, DecisionPipeline, MarketContext
from astra_bot.decision.htf_shadow import htf_bias
from astra_bot.strategies.base import BaseStrategy, Signal, StrategyConfig

START_MS = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
H1 = 3_600_000
H4 = 4 * H1


# ----------------------------------------------------------------- helpers
def _candles(
    symbol: str,
    tf: str,
    n: int,
    start_price: float,
    factor: float,
    step_ms: int,
):
    """Детерминированный геометрический тренд без шума."""
    out = []
    p = start_price
    for i in range(n):
        p *= factor
        o = p / (factor**0.5)
        hi = max(o, p) * 1.002
        lo = min(o, p) * 0.998
        out.append(
            models.Candle(
                exchange="x",
                symbol=symbol,
                timeframe=tf,
                open_time=START_MS + i * step_ms,
                open=Decimal(str(round(o, 6))),
                high=Decimal(str(round(hi, 6))),
                low=Decimal(str(round(lo, 6))),
                close=Decimal(str(round(p, 6))),
                volume=Decimal("10"),
                quote_volume=Decimal("1"),
            )
        )
    return out


def _bearish_4h(n: int = 120):
    return _candles("ALT-USDT", "4h", n, 100.0, 0.995, H4)


def _bullish_1h(n: int = 260):
    return _candles("ALT-USDT", "1h", n, 100.0, 1.002, H1)


class _LongStub(BaseStrategy):
    """Всегда отдаёт лонг-сигнал (имя — параметром, для флип-теста)."""

    def __init__(self, name: str = "plain_stub"):
        super().__init__(StrategyConfig(name=name))

    def calculate_stop_loss(self, *a, **k):
        return Decimal("0")

    def calculate_take_profit(self, *a, **k):
        return []

    async def evaluate(self, symbol, candles, **kwargs):
        if len(candles) < 60:
            return None
        price = Decimal(str(candles[-1].close))
        return Signal(
            symbol=symbol,
            strategy_name=self.name,
            direction=models.TradeDirection.LONG,
            entry_price=price,
            stop_loss=price * Decimal("0.98"),
            take_profit=price * Decimal("1.04"),
            confidence=0.8,
        )


def _ctx(tmp_path, primary=None, h4=None):
    return MarketContext(
        symbol="ALT-USDT",
        current_price=(primary or _bullish_1h())[-1].close,
        candles={"1h": primary or _bullish_1h(), "4h": h4 or _bearish_4h()},
    )


def _pipeline(tmp_path, strategies, **cfg_over):
    cfg = DecisionConfig(htf_shadow_log_path=str(tmp_path / "shadow.jsonl"), **cfg_over)
    return DecisionPipeline(cfg, strategies=strategies)


def _read_bans(tmp_path):
    p = tmp_path / "shadow.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------ pure: bias
def test_htf_bias_bear_bull_neutral_failopen():
    bear = [100.0 * 0.995**i for i in range(120)]  # вниз
    bull = [100.0 * 1.005**i for i in range(120)]  # вверх
    bias, diag = htf_bias(bear)
    assert bias == "short" and "reason" not in diag
    bias, diag = htf_bias(bull)
    assert bias == "long" and "reason" not in diag
    # флэт: ЕМА переплетены вокруг цены -> нейтрально
    flat = [100.0] * 120
    bias, diag = htf_bias(flat)
    assert bias is None and diag.get("reason") == "neutral"
    # мало баров -> fail-open
    bias, diag = htf_bias(bear[:30])
    assert bias is None and diag.get("reason") == "fail_open_bars"


# --------------------------------------------- а) не блокирует, пишет
async def test_shadow_records_ban_but_does_not_block(tmp_path):
    pipe_on = _pipeline(tmp_path, [_LongStub()])
    ctx = _ctx(tmp_path)
    decision_on = await pipe_on.decide(ctx)

    bans = _read_bans(tmp_path)
    assert len(bans) == 1, "против медвежьего 4h лонг записан как гипотетический запрет"
    row = bans[0]
    assert row["symbol"] == "ALT-USDT"
    assert row["direction"] == "long"
    assert row["htf_bias"] == "short"
    assert row["strategy"] == "plain_stub"
    # поля кандидата для будущих R-замеров (решение владельца 12.09)
    assert float(row["entry_price"]) > 0
    assert float(row["stop_loss"]) < float(row["entry_price"])
    assert float(row["take_profit"]) > float(row["entry_price"])
    assert row["rr"] > 0
    assert row["ema_fast"] < row["ema_slow"]
    assert row["bar_time"] > 0

    # Главное свойство фазы 0: решение пайплайна НЕ изменилось.
    pipe_off = _pipeline(tmp_path, [_LongStub()], htf_shadow_enabled=False)
    decision_off = await pipe_off.decide(ctx)
    assert decision_on.action == decision_off.action
    assert decision_on.reasons == decision_off.reasons
    assert decision_on.action in {"LONG", "NO_TRADE"}


# ---------------------------------------------------- б) флипы исключены
async def test_shadow_skips_flip_strategies(tmp_path):
    flip = _LongStub(name="ts_momentum")
    flip_cross = _LongStub(name="ts_momentum_cross")
    pipe = _pipeline(tmp_path, [flip, flip_cross])
    await pipe.decide(_ctx(tmp_path))
    assert _read_bans(tmp_path) == [], "флип-стратегии вне HTF-фильтра"

    # контроль: тот же сигнал, но НЕ-флип имя — запись появляется
    pipe2 = _pipeline(tmp_path, [_LongStub(name="plain_stub")])
    await pipe2.decide(_ctx(tmp_path))
    assert len(_read_bans(tmp_path)) == 1


# ------------------------------------------------- в) fail-open и флаг
async def test_shadow_fail_open_on_few_4h_bars(tmp_path):
    pipe = _pipeline(tmp_path, [_LongStub()])
    ctx = _ctx(tmp_path, h4=_bearish_4h(n=25))  # < 60 закрытых баров
    decision = await pipe.decide(ctx)
    assert _read_bans(tmp_path) == [], "fail-open: фильтр молчит без 60 баров"
    assert decision.action in {"LONG", "NO_TRADE"}


async def test_shadow_disabled_flag_writes_nothing(tmp_path):
    pipe = _pipeline(tmp_path, [_LongStub()], htf_shadow_enabled=False)
    await pipe.decide(_ctx(tmp_path))
    assert _read_bans(tmp_path) == []


async def test_shadow_neutral_4h_writes_nothing(tmp_path):
    pipe = _pipeline(tmp_path, [_LongStub()])
    flat_4h = _candles("ALT-USDT", "4h", 120, 100.0, 1.0, H4)
    await pipe.decide(_ctx(tmp_path, h4=flat_4h))
    assert _read_bans(tmp_path) == []


# ---------------------------------------------------------------- дедуп
async def test_shadow_dedup_same_bar(tmp_path):
    pipe = _pipeline(tmp_path, [_LongStub()])
    ctx = _ctx(tmp_path)
    await pipe.decide(ctx)
    await pipe.decide(ctx)  # тот же бар, та же стратегия
    assert len(_read_bans(tmp_path)) == 1
