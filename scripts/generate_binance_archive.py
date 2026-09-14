#!/usr/bin/env python3
"""Генерация синтетического архива в формате data.binance.vision для локального реплея.

Используется, когда официальный архив недоступен из песочницы (egress-фильтр).
Формат файлов полностью совместим с scripts/track_c_replay.py --source binance-vision.
Цены — детерминированный random walk с режимами, чтобы Z3/Z4/Z6/Z7 давали сигналы.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

SYMBOLS = {
    "BTC": {"base": 20000.0, "seed": 20210101, "vol": 0.008},
    "ETH": {"base": 1500.0, "seed": 20210202, "vol": 0.010},
    "SOL": {"base": 30.0, "seed": 20210303, "vol": 0.012},
}
TIMEFRAMES = {
    "1h": 3600,
    "4h": 14400,
}
START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)

HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore"


def gen_series(base_price: float, seed: int, vol: float, step_sec: int, start: datetime, end: datetime):
    rng = random.Random(seed)
    price = base_price
    drift = 0.0
    t = start
    bars = []
    i = 0
    while t <= end:
        if i % 400 == 0:
            drift = rng.choice((-0.0008, -0.0003, 0.0, 0.0003, 0.0008))
        r = drift + rng.gauss(0.0, vol)
        o = price
        price = max(1.0, price * (1.0 + r))
        c = price
        # high/low with small wick
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, vol * 0.3)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, vol * 0.3)))
        vol_amt = rng.uniform(10, 1000) * (1.0 + abs(r) * 20)
        open_ms = int(t.timestamp() * 1000)
        close_ms = open_ms + step_sec * 1000 - 1
        # Binance format: open_time,open,high,low,close,volume,close_time,quote_volume,count,...
        quote_vol = vol_amt * c
        line = f"{open_ms},{o:.6f},{hi:.6f},{lo:.6f},{c:.6f},{vol_amt:.6f},{close_ms},{quote_vol:.6f},100,50,50000,0"
        bars.append((t, line))
        t += timedelta(seconds=step_sec)
        i += 1
    return bars


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/tmp/binance_vision", help="корень архива")
    args = ap.parse_args()
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    for sym, cfg in SYMBOLS.items():
        sym_usdt = f"{sym}USDT"
        for tf, step in TIMEFRAMES.items():
            print(f"Generating {sym_usdt} {tf} ...")
            bars = gen_series(cfg["base"], cfg["seed"] + (0 if tf == "1h" else 1000), cfg["vol"], step, START, END)
            # group by year-month
            by_month: dict[tuple[int, int], list[str]] = {}
            for dt, line in bars:
                key = (dt.year, dt.month)
                by_month.setdefault(key, []).append(line)
            # write monthly CSVs
            for (y, m), lines in sorted(by_month.items()):
                # path like klines/spot/BTCUSDT/4h/BTCUSDT-4h-YYYY-MM.csv
                dir_path = out_root / "klines" / "spot" / sym_usdt / tf
                dir_path.mkdir(parents=True, exist_ok=True)
                file_path = dir_path / f"{sym_usdt}-{tf}-{y:04d}-{m:02d}.csv"
                file_path.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
            print(f"  {sym_usdt} {tf}: {len(bars)} bars, {len(by_month)} months")

    print(f"\nArchive ready at {out_root}")
    # also create a flat manifest for debugging
    total_files = list(out_root.rglob("*.csv"))
    print(f"Total files: {len(total_files)}")


if __name__ == "__main__":
    main()
