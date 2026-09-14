"""Гейты входа «не торгуй в шуме» (PR #78, часть 3).

Живой режим выключен по умолчанию — это обязательное условие окна фиксации #76,
поэтому первые тесты проверяют именно дефолты, а потом уже механику.
"""

from __future__ import annotations

from types import SimpleNamespace

from astra_bot.decision.entry_gates import (
    DEFAULT_BLOCK_REGIMES,
    DEFAULT_MAX_COSTS_R,
    EntryGateConfig,
    evaluate,
    pre_entry_costs_r,
)
from astra_bot.decision.trading_engine import TradingEngineConfig


# ------------------------------------------------------------------ дефолты ---
def test_default_off_and_shadow_on() -> None:
    cfg = TradingEngineConfig()
    assert cfg.entry_gates_enabled is False, "живой гейт обязан быть выключен (окно #76)"
    assert cfg.entry_gates_shadow_enabled is True, "тень — чистая телеметрия, она включена"
    assert cfg.entry_gate_block_regimes == DEFAULT_BLOCK_REGIMES == frozenset({"LOW_VOLATILITY"})
    assert cfg.entry_gate_max_costs_r == DEFAULT_MAX_COSTS_R == 0.25


def test_from_engine_config_missing_fields_defaults_off() -> None:
    gate = EntryGateConfig.from_engine_config(SimpleNamespace())
    assert gate.enabled is False and gate.shadow_enabled is True
    assert gate.active is True  # тень активна


def test_from_engine_config_string_and_set_regimes() -> None:
    gate = EntryGateConfig.from_engine_config(
        SimpleNamespace(
            entry_gates_enabled=True,
            entry_gates_shadow_enabled=False,
            entry_gate_block_regimes="LOW_VOLATILITY, RANGE",
            entry_gate_max_costs_r=0.4,
        )
    )
    assert gate.enabled and not gate.shadow_enabled
    assert gate.block_regimes == frozenset({"LOW_VOLATILITY", "RANGE"})
    assert gate.max_costs_r == 0.4
    both = EntryGateConfig.from_engine_config(
        SimpleNamespace(entry_gate_block_regimes=["PANIC"])
    )
    assert both.block_regimes == frozenset({"PANIC"})


# ---------------------------------------------------------------- costs_r -----
def test_costs_r_matches_the_trade_record_formula() -> None:
    # entry 100, stop 99 → stop_pct 1%; fee 0.05% + slip 0.1% → 2×0.15%/1% = 0.30R
    assert abs(pre_entry_costs_r(entry=100.0, stop=99.0, fee_pct=0.0005, slippage_pct=0.001) - 0.30) < 1e-9


def test_costs_r_guards_bad_geometry() -> None:
    assert pre_entry_costs_r(entry=100.0, stop=100.0, fee_pct=0.0005, slippage_pct=0.001) is None
    assert pre_entry_costs_r(entry=0.0, stop=99.0, fee_pct=0.0005, slippage_pct=0.001) is None
    assert pre_entry_costs_r(entry=None, stop=99.0, fee_pct=0.0005, slippage_pct=0.001) is None


# ------------------------------------------------------------------ вердикты ---
def test_regime_gate_blocks_low_volatility() -> None:
    gate = EntryGateConfig(enabled=True)
    v = evaluate(
        entry=100.0, stop=98.0, regime_info={"regime": "LOW_VOLATILITY", "confidence": 0.9},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert v.blocked and v.code == "REGIME" and v.live
    assert v.tag == "BLOCKED"
    assert "rejection_stage=entry_gate" in v.reasons()


def test_shadow_mode_counts_but_does_not_block() -> None:
    gate = EntryGateConfig(enabled=False, shadow_enabled=True)
    v = evaluate(
        entry=100.0, stop=98.0, regime_info="LOW_VOLATILITY",
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert v.blocked and not v.live and v.tag == "SHADOW"


def test_other_regime_passes() -> None:
    gate = EntryGateConfig(enabled=True)
    v = evaluate(
        entry=100.0, stop=98.0, regime_info={"regime": "BULL_TREND"},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert not v.blocked and v.costs_r is not None and v.tag == "PASS"


def test_vola_floor_gate_on_costs() -> None:
    # стоп 0.05% → costs_r = 2×0.15%/0.05% = 6R — «четверть R на издержки» превышена
    gate = EntryGateConfig(enabled=True)
    v = evaluate(
        entry=100.0, stop=99.95, regime_info={"regime": "RANGE"},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert v.blocked and v.code == "COSTS_R" and abs(v.costs_r - 6.0) < 1e-6
    # широкий стоп (2%) → 0.15R < 0.25R → проходим
    ok = evaluate(
        entry=100.0, stop=98.0, regime_info={"regime": "RANGE"},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert not ok.blocked


def test_regime_gate_wins_over_costs_gate() -> None:
    gate = EntryGateConfig(enabled=True)
    v = evaluate(
        entry=100.0, stop=99.95, regime_info={"regime": "LOW_VOLATILITY"},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert v.code == "REGIME"


def test_fail_open_on_garbage() -> None:
    gate = EntryGateConfig(enabled=True)
    v = evaluate(
        entry=100.0, stop=None, regime_info=object(),
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert not v.blocked


def test_inactive_config_does_not_even_compute() -> None:
    gate = EntryGateConfig(enabled=False, shadow_enabled=False)
    v = evaluate(
        entry=100.0, stop=99.95, regime_info="LOW_VOLATILITY",
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    assert not v.blocked and v.costs_r is None


def test_log_line_carries_keyword_and_numbers() -> None:
    gate = EntryGateConfig(enabled=False, shadow_enabled=True, max_costs_r=0.25)
    v = evaluate(
        entry=100.0, stop=99.95, regime_info={"regime": "RANGE"},
        fee_pct=0.0005, slippage_pct=0.001, cfg=gate,
    )
    line = v.log_line("BTC-USDT")
    assert "ENTRY_GATE SHADOW BTC-USDT" in line and "gate=COSTS_R" in line and "costs_r=6.000" in line
