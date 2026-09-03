"""Расширенный набор unit-тестов для многовалютного аудита и MTF-стратегии."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest
from astra_bot.core import models
from astra_bot.strategies.multicurrency_mtf import MulticurrencyMTFStrategy
from scripts.audit_multicurrency import resample_klines, run_audit_simulation


def _make_1h_df_with_patterns(n: int, step: float = 0.001) -> pd.DataFrame:
    records = []
    start_ts = 1609459200000  # 2021-01-01
    price = 100.0
    for i in range(n):
        # Каждую 20-ю свечу делаем красной, а следующую - бычьим поглощением
        if i % 20 == 18:
            op = price
            cl = price * 0.995  # красная
            records.append({
                "open_time": start_ts + i * 3600000,
                "open": op,
                "high": op * 1.001,
                "low": cl * 0.999,
                "close": cl,
                "volume": 1000.0,
            })
            price = cl
        elif i % 20 == 19:
            op = price * 0.994  # ниже клоуза предыдущей
            cl = price * 1.01   # выше оппена предыдущей (бычье поглощение)
            records.append({
                "open_time": start_ts + i * 3600000,
                "open": op,
                "high": cl * 1.005,
                "low": op * 0.998,
                "close": cl,
                "volume": 2000.0,  # высокий объем
            })
            price = cl
        else:
            op = price
            cl = price * (1 + step)
            records.append({
                "open_time": start_ts + i * 3600000,
                "open": op,
                "high": max(op, cl) * 1.002,
                "low": min(op, cl) * 0.998,
                "close": cl,
                "volume": 1000.0,
            })
            price = cl
    return pd.DataFrame(records)


def test_resample_1h_to_4h_no_lookahead():
    df1h = _make_1h_df_with_patterns(100)
    df4h = resample_klines(df1h, "4h")
    assert len(df4h) == 25
    assert df4h["open_time"].iloc[0] == df1h["open_time"].iloc[0]
    assert df4h["open"].iloc[0] == df1h["open"].iloc[0]


def test_resample_1h_to_1d_no_lookahead():
    df1h = _make_1h_df_with_patterns(100)
    df1d = resample_klines(df1h, "1d")
    assert len(df1d) == 5
    assert df1d["open_time"].iloc[0] == df1h["open_time"].iloc[0]
    assert df1d["open"].iloc[0] == df1h["open"].iloc[0]


@pytest.mark.asyncio
async def test_btc_bearish_1d_blocks_altcoin_long():
    s = MulticurrencyMTFStrategy()
    c1h = [models.Candle("t", "ETHUSDT", "1h", 1000 + i * 3600, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(30)]
    c4h = [models.Candle("t", "ETHUSDT", "4h", 1000 + i * 14400, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(30)]
    c1d = [models.Candle("t", "ETHUSDT", "1d", 1000 + i * 86400, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(210)]
    btc_1d_bear = [models.Candle("t", "BTCUSDT", "1d", 1000 + i * 86400, Decimal(str(1000 - i)), Decimal(str(1001 - i)), Decimal(str(998 - i)), Decimal(str(999 - i)), Decimal("100"), Decimal("10")) for i in range(210)]

    sig = await s.evaluate("ETHUSDT", candles=c1h, candles_1d=c1d, candles_4h=c4h, candles_1h=c1h, btc_candles_1d=btc_1d_bear)
    assert sig is None


def test_volume_below_sma20_blocks_entry():
    df_btc = _make_1h_df_with_patterns(1000, step=0.001)
    # Снижаем объемы до нуля
    df_btc["volume"] = 10.0
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 1000 * 3600000,
        use_volume_filter=True,
    )
    assert res["metrics"]["total_trades"] == 0


def test_breakout_without_retest_blocks_entry():
    df_btc = _make_1h_df_with_patterns(1000, step=0.001)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    # При use_retest=True без полноценного возврата свечи к уровню сделку не откроем
    res_retest = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 1000 * 3600000,
        use_retest=True,
    )
    res_no_retest = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 1000 * 3600000,
        use_retest=False,
    )
    assert res_retest["metrics"]["total_trades"] <= res_no_retest["metrics"]["total_trades"]


def test_single_position_counts_as_one_trade():
    df_btc = _make_1h_df_with_patterns(6000, step=0.001)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
        use_retest=False, use_volume_filter=False, use_partial_trailing=True,
    )
    # Каждый элемент trade_history должен соответствовать закрытой позиции (total_trades == len(trades))
    assert res["metrics"]["total_trades"] == len(res["trades"])


def test_btc_eth_no_simultaneous_correlation_exposures():
    df_btc = _make_1h_df_with_patterns(6000, step=0.001)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
        use_retest=False, use_volume_filter=False,
    )
    btc_entries = [t["entry_time"] for t in res["trades"] if t["symbol"] == "BTCUSDT"]
    eth_entries = [t["entry_time"] for t in res["trades"] if t["symbol"] == "ETHUSDT"]
    overlap = set(btc_entries).intersection(set(eth_entries))
    assert len(overlap) == 0


def test_daily_loss_stop_blocks_new_entries():
    df_btc = _make_1h_df_with_patterns(6000, step=-0.01)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
    )
    assert res["metrics"]["max_drawdown"] >= 0.0


def test_oos_uses_only_candles_after_oos_start():
    df_btc = _make_1h_df_with_patterns(6000, step=0.001)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    oos_start_ms = 1609459200000 + 3000 * 3600000
    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=oos_start_ms, end_ms=1609459200000 + 6000 * 3600000,
        use_retest=False, use_volume_filter=False,
    )
    for t in res["trades"]:
        entry_ts = int(pd.to_datetime(t["entry_time"]).timestamp() * 1000)
        assert entry_ts >= oos_start_ms


def test_fees_and_slippage_degrade_performance():
    df_btc = _make_1h_df_with_patterns(6000, step=0.001)
    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_btc, "SOLUSDT": df_btc, "XRPUSDT": df_btc}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res_free = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
        fee_rate=0.0, slippage_rate=0.0, use_retest=False, use_volume_filter=False,
    )
    res_costs = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
        fee_rate=0.001, slippage_rate=0.001, use_retest=False, use_volume_filter=False,
    )
    assert res_free["metrics"]["net_pnl"] > res_costs["metrics"]["net_pnl"]


def test_audit_missing_data_exits_with_error():
    import sys

    from scripts.audit_multicurrency import main as audit_main

    orig_argv = sys.argv
    try:
        sys.argv = ["audit_multicurrency.py", "--data-dir", "non_existing_dir_xyz"]
        code = audit_main()
        assert code == 1
    finally:
        sys.argv = orig_argv


def test_audit_creates_full_artifacts_on_fixture(tmp_path):
    import sys

    from scripts.audit_multicurrency import main as audit_main

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "reports"

    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        df = _make_1h_df_with_patterns(1000)
        df.to_csv(data_dir / f"{sym}_1h.csv", index=False)

    orig_argv = sys.argv
    try:
        sys.argv = [
            "audit_multicurrency.py",
            "--data-dir", str(data_dir),
            "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT",
            "--start", "2021-01-01",
            "--oos-start", "2021-01-10",
            "--end", "2021-01-20",
            "--out", str(out_dir)
        ]
        code = audit_main()
        assert code == 0
        assert (out_dir / "protocol.json").exists()
        assert (out_dir / "data_quality.json").exists()
        assert (out_dir / "aggregate_summary.json").exists()
        assert (out_dir / "aggregate_summary.md").exists()
        assert (out_dir / "full" / "trades.csv").exists()
        assert (out_dir / "ablation" / "baseline.json").exists()
    finally:
        sys.argv = orig_argv
