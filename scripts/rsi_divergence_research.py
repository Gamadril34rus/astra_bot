"""D6: исследование RSI-расхождений на истории (ЗАКРЫТЫЕ бары, свинг-точки).

Гипотеза владельца: «RSI-расхождения: если они есть — с большей
вероятностью график пойдёт наоборот».

Детектор (только закрытые бары, без lookahead):
  * RSI(14) по Уайлдеру на закрытиях;
  * свинг-пивоты — фракталы с 2 бар слева/справа (пивот подтверждается
    только через 2 закрытых бара);
  * медвежье расхождение: цена выше-выше (второй пивот-хай выше первого),
    RSI на втором пивоте НИЖЕ первого минимум на RS_MIN;
  * бычье — зеркально по пивот-лоу.

Замер: частота, распределение по годам, «разворотность» (доля движений
в сторону сигнала на горизонтах 6/12/24 бара) и средний ход, а также
грубая симуляция схемы 1R-стоп / 2R-тейк от close бара подтверждения.

Запуск:  python scripts/rsi_divergence_research.py [csv_path]
По умолчанию — data/BTCUSDT_4h.csv (локальная выгрузка, 2021-…).
Публичный API BingX из песочницы недоступен — живую докачку не делаем.
"""

from __future__ import annotations

import csv
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

RSI_PERIOD = 14
PIVOT_K = 2  # фракталь: 2 бара слева/справа
RS_MIN = 2.0  # минимальная дельта RSI между пивотами
MAX_GAP_BARS = 30  # макс. расстояние между пивотами
HORIZONS = (6, 12, 24)  # бары вперёд (4h: 1/2/4 суток)
STOP_PCT = 1.0  # симуляция: стоп 1% от close подтверждения
RR = 2.0  # тейк 2R


def rsi_wilder(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def pivots_high(lows_highs: list[float], k: int = PIVOT_K) -> list[int]:
    """Индексы пивот-хаёв: high[i] строго выше k соседей с обеих сторон."""
    out = []
    for i in range(k, len(lows_highs) - k):
        w = lows_highs[i - k: i + k + 1]
        if lows_highs[i] == max(w) and w.count(lows_highs[i]) == 1:
            out.append(i)
    return out


def pivots_low(vals: list[float], k: int = PIVOT_K) -> list[int]:
    out = []
    for i in range(k, len(vals) - k):
        w = vals[i - k: i + k + 1]
        if vals[i] == min(w) and w.count(vals[i]) == 1:
            out.append(i)
    return out


def find_divergences(
    highs: list[float], lows: list[float], rsi: list[float | None]
) -> list[dict]:
    """Список расхождений; сигнал подтверждается на баре i+PIVOT_K."""
    sigs: list[dict] = []
    # Медвежьи: цена HH, RSI lower-high.
    ph = pivots_high(highs)
    for a, b in zip(ph, ph[1:]):
        if b - a > MAX_GAP_BARS:
            continue
        ra, rb = rsi[a], rsi[b]
        if ra is None or rb is None:
            continue
        if highs[b] > highs[a] and rb <= ra - RS_MIN:
            sigs.append({"type": "bearish", "pivot": b, "confirm": b + PIVOT_K,
                         "rsi_delta": ra - rb})
    # Бычьи: цена LL, RSI higher-low.
    pl = pivots_low(lows)
    for a, b in zip(pl, pl[1:]):
        if b - a > MAX_GAP_BARS:
            continue
        ra, rb = rsi[a], rsi[b]
        if ra is None or rb is None:
            continue
        if lows[b] < lows[a] and rb >= ra + RS_MIN:
            sigs.append({"type": "bullish", "pivot": b, "confirm": b + PIVOT_K,
                         "rsi_delta": rb - ra})
    return sigs


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/BTCUSDT_4h.csv")
    rows = list(csv.DictReader(path.open()))
    ts = [int(r["open_time"]) for r in rows]
    opens = [float(r["open"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    closes = [float(r["close"]) for r in rows]
    n = len(closes)
    rsi = rsi_wilder(closes)
    sigs = [s for s in find_divergences(highs, lows, rsi) if s["confirm"] < n]

    print(f"файл: {path}  баров: {n}  "
          f"({datetime.fromtimestamp(ts[0] / 1000, tz=timezone.utc):%Y-%m-%d} .. "
          f"{datetime.fromtimestamp(ts[-1] / 1000, tz=timezone.utc):%Y-%m-%d}, TF=4h)")
    print(f"RSI={RSI_PERIOD}, пивот k={PIVOT_K} (подтверждение +{PIVOT_K} бара), "
          f"RS_MIN={RS_MIN}, межпивот <= {MAX_GAP_BARS} баров")

    for kind in ("bearish", "bullish"):
        sub = [s for s in sigs if s["type"] == kind]
        if not sub:
            continue
        sign = -1.0 if kind == "bearish" else 1.0  # сигнал = ход ПРОТИВ тренда расхождения
        print(f"\n=== {kind.upper()}: {len(sub)} сигналов "
              f"({len(sub) / n * 100:.2f}% баров истории) ===")
        # по годам
        per_year: dict[int, int] = {}
        for s in sub:
            y = datetime.fromtimestamp(ts[s["confirm"]] / 1000, tz=timezone.utc).year
            per_year[y] = per_year.get(y, 0) + 1
        print("  по годам:", dict(sorted(per_year.items())))
        print(f"  дельта RSI: медиана {statistics.median(s['rsi_delta'] for s in sub):.1f}")
        for h in HORIZONS:
            fwd = []
            for s in sub:
                i = s["confirm"]
                if i + h < n:
                    fwd.append(sign * (closes[i + h] / closes[i] - 1) * 100)
            if fwd:
                win = sum(1 for x in fwd if x > 0)
                print(f"  горизонт {h} баров ({h * 4}ч): n={len(fwd)} "
                      f"доля в сторону сигнала={win / len(fwd) * 100:.1f}% "
                      f"средний ход={statistics.mean(fwd):+.2f}% "
                      f"медиана={statistics.median(fwd):+.2f}%")
        # Симуляция 1R-стоп/2R-тейк от close подтверждения (внутри окна 30 баров).
        wins = losses = none = 0
        for s in sub:
            i = s["confirm"]
            entry = closes[i]
            stop = entry * (1 - sign * STOP_PCT / 100)
            take = entry * (1 + sign * STOP_PCT * RR / 100)
            res = None
            for j in range(i + 1, min(i + 31, n)):
                if sign == 1:
                    if lows[j] <= stop:
                        res = False
                        break
                    if highs[j] >= take:
                        res = True
                        break
                else:
                    if highs[j] >= stop:
                        res = False
                        break
                    if lows[j] <= take:
                        res = True
                        break
            if res is None:
                none += 1
            elif res:
                wins += 1
            else:
                losses += 1
        tot = wins + losses
        if tot:
            print(f"  симуляция стоп-{STOP_PCT}%/тейк-{RR:.0f}R (окно 30 баров): "
                  f"win={wins} loss={losses} не-исполнилось={none} "
                  f"WR={wins / tot * 100:.1f}% EV={((wins - losses) / tot) * (RR - 1) + 0:+.2f}R/сделку"
                  .replace("+0.00R/сделку", f"{(wins * RR - losses) / tot:+.2f}R/сделку"))
    print("\nмодель сигнала: только ЗАКРЫТЫЕ бары; пивот подтверждён k барами — "
          "в live-семантике сигнал доступен на сессии, следующей за confirm-баром")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
