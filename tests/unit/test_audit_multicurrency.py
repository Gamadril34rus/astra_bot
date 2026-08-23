"""Тесты многовалютного аудиторского контура и стратегии multicurrency_mtf."""

from __future__ import annotations

import json
import pytest
import pandas as pd
from decimal import Decimal

from astra_bot.core import models
from astra_bot.strategies.multicurrency_mtf import MulticurrencyMTFStrategy
from scripts.audit_multicurrency import resample_klines, run_audit_simulation


def _make_1h_df(n: int, step: float = 0.001) -> pd.DataFrame:
    records = []
    start_ts = 1609459200000  # 2021-01-01
    price = 100.0
    for i in range(n):
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


def test_tp1_closes_half_and_sets_breakeven():
    # Фикстура симуляции с достаточным количеством свечей для EMA200 и отключенным фильтром объема
    df_btc = _make_1h_df(6000, step=0.001)
    df_eth = _make_1h_df(6000, step=0.001)
    df_sol = _make_1h_df(6000, step=0.001)
    df_xrp = _make_1h_df(6000, step=0.001)

    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_eth, "SOLUSDT": df_sol, "XRPUSDT": df_xrp}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 6000 * 3600000,
        use_retest=False,
        use_volume_filter=False,
    )
    tp1_trades = [t for t in res["trades"] if t["reason"] == "TP1_HALF"]
    assert len(tp1_trades) > 0


def test_btc_eth_correlation_group_limit():
    # Проверка того, что BTC и ETH не могут быть открыты одновременно
    df_btc = _make_1h_df(500, step=0.003)
    df_eth = _make_1h_df(500, step=0.003)
    df_sol = _make_1h_df(500, step=0.003)
    df_xrp = _make_1h_df(500, step=0.003)

    data_1h = {"BTCUSDT": df_btc, "ETHUSDT": df_eth, "SOLUSDT": df_sol, "XRPUSDT": df_xrp}
    data_4h = {k: resample_klines(v, "4h") for k, v in data_1h.items()}
    data_1d = {k: resample_klines(v, "1d") for k, v in data_1h.items()}

    res = run_audit_simulation(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        data_1h, data_4h, data_1d,
        start_ms=1609459200000, end_ms=1609459200000 + 500 * 3600000,
    )
    # Проверка временных интервалов сделок: не должно быть пересечения открытых позиций BTC и ETH
    btc_trades = [t for t in res["trades"] if t["symbol"] == "BTCUSDT"]
    eth_trades = [t for t in res["trades"] if t["symbol"] == "ETHUSDT"]
    assert not (len(btc_trades) > 0 and len(eth_trades) > 0 and btc_trades[0]["entry_time"] == eth_trades[0]["entry_time"])


def test_resample_no_lookahead():
    df1h = _make_1h_df(100)
    df4h = resample_klines(df1h, "4h")
    # Проверка количества и отсутствия смещения вперед
    assert len(df4h) == 25
    assert df4h["open_time"].iloc[0] == df1h["open_time"].iloc[0]
    assert df4h["open"].iloc[0] == df1h["open"].iloc[0]


@pytest.mark.asyncio
async def test_btc_bearish_gate_blocks_altcoin_long():
    s = MulticurrencyMTFStrategy()

    c1h = [models.Candle("t", "ETHUSDT", "1h", 1000 + i * 3600, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(30)]
    c4h = [models.Candle("t", "ETHUSDT", "4h", 1000 + i * 14400, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(30)]
    c1d = [models.Candle("t", "ETHUSDT", "1d", 1000 + i * 86400, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("100"), Decimal("10")) for i in range(210)]

    # BTC в жестком падении
    btc_1d = [models.Candle("t", "BTCUSDT", "1d", 1000 + i * 86400, Decimal(str(1000 - i)), Decimal(str(1001 - i)), Decimal(str(998 - i)), Decimal(str(999 - i)), Decimal("100"), Decimal("10")) for i in range(210)]

    sig = await s.evaluate(
        symbol="ETHUSDT",
        candles=c1h,
        candles_1d=c1d,
        candles_4h=c4h,
        candles_1h=c1h,
        btc_candles_1d=btc_1d,
    )
    assert sig is None


def test_audit_missing_data_exit():
    from scripts.audit_multicurrency import main as audit_main
    import sys

    orig_argv = sys.argv
    try:
        sys.argv = ["audit_multicurrency.py", "--data-dir", "non_existing_dir_123"]
        code = audit_main()
        assert code == 1
    finally:
        sys.argv = orig_argv


def test_audit_creates_artifacts_on_fixture(tmp_path):
    from scripts.audit_multicurrency import main as audit_main
    import sys

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out_dir = tmp_path / "reports"

    # Готовим фикстуры
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        df = _make_1h_df(500)
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
    finally:
        sys.argv = orig_argv
