"""Гипотетический R отклонённого кандидата (PR #78, часть 3).

Это единственная цифра, которая позволяет оценить гейт «не торгуй в шуме» ДО
его включения: чем торговал бы бот, если бы вход не был отклонён. Правила
жёстко зафиксированы (репер — close сигнального бара, стоп приоритетнее тейка
в одном баре, gross без комиссий), поэтому проверяем их прямо.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from astra_bot.core import models
from astra_bot.ml.no_trade_observations import (
    NoTradeObservationLog,
    _hypothetic_r,
    make_observation_id,
)

STEP = 3600
# Индекс исходов режется до 30 дней (OUTCOMES_KEEP_DAYS) — берём свежий, но
# полностью закрытый диапазон, иначе _prune_outcomes съест запись до сохранения.
T0 = (int(time.time()) // STEP - 400) * STEP


def _c(i: int, *, o: float, h: float, l: float, c: float) -> models.Candle:
    return models.Candle(
        exchange="feed", symbol="BTC-USDT", timeframe="1h", open_time=T0 + i * STEP,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
        close=Decimal(str(c)), volume=Decimal("1000"), quote_volume=Decimal("1e6"),
    )


def _series(bars: list[tuple[float, float, float, float]]) -> list[models.Candle]:
    # сигнальный бар — index 0, дальше — «будущее»
    out = [_c(0, o=100.0, h=100.0, l=100.0, c=100.0)]
    for k, (o, h, l, c) in enumerate(bars, start=1):
        out.append(_c(k, o=o, h=h, l=l, c=c))
    return out


CAND = {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 104.0, "direction": "long"}


def test_signal_bar_close_is_the_reference_and_stop_hits_first() -> None:
    # риск 2.0; бар 1 касается стопа 98.0 → ровно -1R
    bars = [(100.0, 100.2, 97.9, 99.0)]
    hyp = _hypothetic_r(_series(bars), T0, CAND, max_bars=24)
    assert hyp is not None and hyp["r"] == -1.0 and hyp["exit_kind"] == "stop" and hyp["bars"] == 1


def test_take_is_measured_in_multiples_of_risk() -> None:
    # take 104 → дистанция 4.0 = 2R
    bars = [(100.0, 101.0, 99.5, 101.0), (101.0, 104.5, 100.5, 104.2)]
    hyp = _hypothetic_r(_series(bars), T0, CAND, max_bars=24)
    assert hyp is not None and hyp["r"] == 2.0 and hyp["exit_kind"] == "take" and hyp["bars"] == 2


def test_same_bar_touching_both_levels_counts_as_stop() -> None:
    # консервативное правило: касание стопа важнее тейка в одном баре
    bars = [(100.0, 105.0, 97.0, 103.0)]
    hyp = _hypothetic_r(_series(bars), T0, CAND, max_bars=24)
    assert hyp is not None and hyp["exit_kind"] == "stop" and hyp["r"] == -1.0


def test_short_geometry_is_mirrored() -> None:
    cand = {"entry_price": 100.0, "stop_loss": 102.0, "take_profit": 94.0, "direction": "short"}
    bars = [(100.0, 101.0, 99.0, 100.5), (100.5, 101.0, 93.5, 94.0)]
    hyp = _hypothetic_r(_series(bars), T0, cand, max_bars=24)
    assert hyp is not None and hyp["exit_kind"] == "take" and hyp["r"] == 3.0


def test_no_touch_exits_at_window_close_with_incomplete_flag() -> None:
    bars = [(100.0, 100.5, 99.5, 100.4)] * 30
    hyp = _hypothetic_r(_series(bars), T0, CAND, max_bars=6)
    assert hyp is not None and hyp["exit_kind"] == "time" and hyp["bars"] == 6
    # 0.4 / 2.0 риска = +0.2R (gross: комиссий здесь нет намеренно)
    assert abs(hyp["r"] - 0.2) < 1e-9
    # баров в окне достаточно → исход не «недомерен»
    assert not hyp.get("incomplete")
    short_series = _series(bars[:2])
    hyp2 = _hypothetic_r(short_series, T0, CAND, max_bars=24)
    assert hyp2 is not None and hyp2["exit_kind"] == "time" and hyp2["incomplete"] is True


def test_useless_geometry_returns_none_without_raising() -> None:
    series = _series([(100.0, 101.0, 99.0, 100.5)] * 3)
    assert _hypothetic_r(series, T0, None, 24) is None
    assert _hypothetic_r(series, T0, {"entry_price": 100.0, "stop_loss": 100.0}, 24) is None
    assert _hypothetic_r(series, T0, {"entry_price": 0, "stop_loss": 98.0}, 24) is None
    assert _hypothetic_r(series, T0, {"entry_price": "x", "stop_loss": 98.0}, 24) is None
    # сигнального бара нет в серии — мерить не от чего
    assert _hypothetic_r(series, T0 + 10 ** 9, CAND, 24) is None
    # нет будущего
    assert _hypothetic_r(_series([]), T0, CAND, 24) is None


def test_missing_take_profit_is_not_a_crash() -> None:
    cand = {"entry_price": 100.0, "stop_loss": 98.0, "direction": "long"}
    bars = [(100.0, 103.0, 99.0, 102.0)] * 2
    hyp = _hypothetic_r(_series(bars), T0, cand, max_bars=2)
    assert hyp is not None and hyp["exit_kind"] == "time"


# ------------------------------------------------------------------ pipeline ---
def test_enrich_writes_hypothetic_r_and_backfills_jsonl(tmp_path: Path) -> None:
    obs_path = tmp_path / "no_trade_observations.jsonl"
    out_path = tmp_path / "no_trade_outcomes.json"
    bar_time = T0
    reason = "ENTRY_GATE"
    oid = make_observation_id("BTC-USDT", bar_time, reason, "-", "long")
    row = {
        "id": oid, "symbol": "BTC-USDT", "bar_time": bar_time, "timestamp": bar_time,
        "market_regime": "LOW_VOLATILITY", "regime_confidence": 0.8, "reason_code": reason,
        "reasons": ["entry_gate: regime=LOW_VOLATILITY"],
        "candidate": {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 104.0,
                      "direction": "long"},
        "features": {}, "result": None, "rejection_stage": "entry_gate",
    }
    obs_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    log = NoTradeObservationLog(observations_path=obs_path, outcomes_path=out_path)
    series = _series([(100.0, 100.2, 97.9, 99.0)] * 3)
    enriched = log.enrich({"BTC-USDT": series})
    assert [o.id for o in enriched] == [oid], "наблюдение должно быть подхвачено"

    outcome = json.loads(out_path.read_text(encoding="utf-8"))["outcomes"][oid]
    assert outcome["hypothetic_r"]["r"] == -1.0
    assert outcome["hypothetic_r"]["exit_kind"] == "stop"
    # исходы на горизонтах считаются как раньше — рядом, не вместо
    assert "horizons" in outcome

    written = json.loads(obs_path.read_text(encoding="utf-8").strip())
    assert written["rejection_stage"] == "entry_gate"
    assert written["hypothetic_r"]["r"] == -1.0, "backfill обязан донести R в JSONL"
    assert written["result"] is not None


def test_enrich_survives_broken_candidate(tmp_path: Path) -> None:
    """Кривой candidate не имеет права сломать обогащение горизонтов."""
    obs_path = tmp_path / "o.jsonl"
    out_path = tmp_path / "u.json"
    bar_time = T0
    oid = make_observation_id("ETH-USDT", bar_time, "NO_VALID_SETUP", "-", "-")
    obs_path.write_text(
        json.dumps({
            "id": oid, "symbol": "ETH-USDT", "bar_time": bar_time, "timestamp": bar_time,
            "market_regime": "RANGE", "regime_confidence": 0.5,
            "reason_code": "NO_VALID_SETUP", "reasons": [],
            "candidate": {"entry_price": None, "stop_loss": "не число"},
            "features": {}, "result": None, "rejection_stage": None,
        }) + "\n",
        encoding="utf-8",
    )
    log = NoTradeObservationLog(observations_path=obs_path, outcomes_path=out_path)
    # серия ETH: символ наблюдению не совпадает → pending/enrich работают по своей логике, но падения нет
    enriched = log.enrich({"ETH-USDT": [
        models.Candle(
            exchange="feed", symbol="ETH-USDT", timeframe="1h", open_time=T0 + i * STEP,
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
            close=Decimal("100.5"), volume=Decimal("10"), quote_volume=Decimal("1000"),
        )
        for i in range(31)
    ]})
    assert len(enriched) == 1
    outcome = json.loads(out_path.read_text(encoding="utf-8"))["outcomes"][oid]
    assert "hypothetic_r" not in outcome, "непригодная геометрия не пишет нули-обманки"
    assert outcome["horizons"]
