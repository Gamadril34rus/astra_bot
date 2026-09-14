"""Сканер рычагов: что реально улучшило бы P&L, а не подгонка под шум.

Два независимых источника.

1. СТРУКТУРА РЫНКА (n ~ 32k баров 5m, 33 символа, 07-14.09.2026) —
   ``models/no_trade_outcomes.json``. Каждая запись = пропущенный сигнал и его
   форвард: ``future_return`` (close к close на горизонте) и ``max_up``/``max_down``
   (экстремумы за окно). У 31 615 записей есть только горизонт 1 бар, поэтому
   длинные горизонты собираются склейкой consecutive-записей по символу:
     * нижняя граница хода = максимум кумулятивного возврата по close;
     * верхняя граница    = максимум (close-возврат до бара + max_up того бара).
   Прямое измерение (n=254..417 с готовыми горизонтами) приводится как сверка.
   Отвечает на вопрос: «покрывает ли типичное движение цикл издержек»
   (cost round-trip = 0.05%x2 комиссия + 0.1%x2 проскальзывание = 0.30%,
   docs/COST_MODEL.md) — и есть ли вообще персистентность, на которую можно
   встать (автокорреляция, momentum vs reversal, дрейф по часам).

2. ФИЛЬТРЫ (n = 184 сделки) — ``models/paper_trades.jsonl``:
   структурные срезы (удержание, costs_r, ширина стопа, оси режима, ТФ) и
   learned-фильтры (стратегия/час/ТФ/направление), оценённые ТОЛЬКО walk-forward:
   правила выводятся на первой половине выборки, применяются ко второй.
   Перестановочный p-value (4000 перестановок) — второй защитный критерий.

Только чтение, конфигурацию не меняет (окно фиксации правил #74-#77).

Запуск:
    python scripts/lever_scan.py [--cost 0.003] [--trades PATH] [--outcomes PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

MOSCOW = timedelta(hours=3)
BAR_MS = 300_000
HORIZONS = (1, 3, 6, 12, 24)
DEFAULT_TRADES = "models/paper_trades.jsonl"
DEFAULT_OUTCOMIES = "models/no_trade_outcomes.json"
PERM = 4000


def msks(ms: int) -> int:
    return int((datetime.fromtimestamp(ms / 1000, UTC) + MOSCOW).hour)


def mean_se(xs: list[float]) -> tuple[float, float]:
    m = statistics.mean(xs)
    return m, statistics.pstdev(xs) / math.sqrt(max(1, len(xs)))


def perm_pvalue(groups: list[list[float]], seed: int = 11, boot: int = PERM) -> float:
    a, b = groups
    if not a or not b:
        return 1.0
    obs = abs(statistics.mean(a) - statistics.mean(b))
    pool = a + b
    rng = random.Random(seed)
    na = len(a)
    hits = 0
    for _ in range(boot):
        rng.shuffle(pool)
        if abs(statistics.mean(pool[:na]) - statistics.mean(pool[na:])) >= obs:
            hits += 1
    return (hits + 1) / (boot + 1)


# ------------------------------------------------------- 1. структура рынка (большая n)
def build_paths(rows: list[dict]) -> dict[str, list[list[tuple[int, float, float]]]]:
    """symbol -> список непрерывных серий [(bar_time, r_next, max_up_next), ...]

    r_next / max_up_next — из горизонта 1 бар: возврат close-к-close и ход до максимума
    СЛЕДУЮЩЕГО бара, оба относительно close текущего.
    """
    by: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        h1 = r.get("horizons", {}).get("1")
        if not h1:
            continue
        by[r["symbol"]][int(r["bar_time"])] = (
            float(h1["future_return"]), float(h1["max_up"]), float(h1["max_down"]))
    out: dict[str, list[list]] = {}
    for sym, m in by.items():
        series: list[list] = []
        cur: list = []
        prev = None
        for t in sorted(m):
            if prev is not None and t - prev == BAR_MS:
                cur.append((t, *m[t]))
            else:
                if len(cur) >= 5:
                    series.append(cur)
                cur = [(t, *m[t])]  # (bar_time, r_next, up_next, dn_next)
            prev = t
        if len(cur) >= 5:
            series.append(cur)
        if series:
            out[sym] = series
    return out


def excursions(paths: dict, cost: float) -> None:
    print("=" * 92)
    print("1) СТРУКТУРА РЫНКА: покрыть цикл издержек")
    n_bars = sum(len(s) for v in paths.values() for s in v)
    print(f"   баров в непрерывных сериях: {n_bars} (символов {len(paths)}), "
          f"cost round-trip = {cost*100:.2f}% цены")
    print(f"\n   {'горизонт':>12}{'точек':>9}{'ход, нижняя':>14}{'ход, верхняя':>14}"
          f"{'cost в этих единицах':>22}{'ход/cost (нижн.)':>19}")
    for h in HORIZONS:
        lo: list[float] = []
        hi: list[float] = []
        for series in paths.values():
            for s in series:
                for i in range(0, len(s) - h):
                    cum = 1.0        # close_j / close_i (до движения бара j)
                    mx = -9e9
                    mxhi = -9e9
                    for j in range(i, i + h):
                        # верхняя граница: high бара j+1 = (1+max_up_j) * close_j
                        mxhi = max(mxhi, cum * (1.0 + s[j][2]) - 1.0)
                        cum *= 1.0 + s[j][1]          # close_{j+1} / close_i
                        mx = max(mx, cum - 1.0)       # нижняя граница: по закрытиям
                    lo.append(mx)
                    hi.append(mxhi)
        if len(lo) < 500:
            continue
        mlo, mhi = statistics.median(lo), statistics.median(hi)
        print(f"   {str(h)+' баров':>12}{len(lo):>9}{mlo*100:>13.2f}%{mhi*100:>13.2f}%"
              f"{'доля >= cost: ' + f'{100*sum(1 for x in lo if x >= cost)/len(lo):.0f}%' + ' / ' + f'{100*sum(1 for x in hi if x >= cost)/len(hi):.0f}%':>22}"
              f"{mlo/cost:>18.2f}x")


def microstructure(paths: dict, rows: list[dict], cost: float) -> None:
    print("\n   есть ли персистентность, на которую можно встать:")
    # 1) автокорреляция 5m доходностей
    lags = []
    for lag in (1, 2, 3, 6, 12):
        pairs: list[tuple[float, float]] = []
        for series in paths.values():
            for s in series:
                r = [x[1] for x in s]
                pairs += [(r[i], r[i + lag]) for i in range(len(r) - lag)]
        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        mx, my = statistics.mean(x), statistics.mean(y)
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / len(x)
        sd = statistics.pstdev(x) * statistics.pstdev(y)
        ac = cov / sd if sd else 0.0
        t = ac * math.sqrt(len(x) - 2) / math.sqrt(max(1e-12, 1 - ac * ac))
        lags.append((lag, ac, t, len(x)))
    print("      автокорреляция доходностей 5m: " + "  ".join(
        f"lag{lg} {ac:+.4f} (t={tt:+.1f})" for lg, ac, tt, _ in lags))

    # 2) momentum vs reversal на 1 и 6 баров
    for h in (1, 6):
        mom: list[float] = []
        rev: list[float] = []
        for series in paths.values():
            for s in series:
                r = [x[1] for x in s]
                for i in range(1, len(r) - h + 1):
                    if r[i - 1] == 0:
                        continue
                    fwd = 1.0
                    for j in range(i, i + h):
                        fwd *= 1.0 + r[j]
                    sign = 1.0 if r[i - 1] > 0 else -1.0
                    mom.append(sign * (fwd - 1.0))
                    rev.append(-sign * (fwd - 1.0))
        if len(mom) < 500:
            continue
        for tag, v in (("по тренду последнего бара", mom), ("против (reversal)", rev)):
            m, se = mean_se(v)
            net = m - cost
            print(f"      {h} бар(ов), {tag:<26}: средний ход {m*100:+.4f}% ± {se*100:.4f} "
                  f"(t={m/se:+.2f}); за вычетом цикла {net*100:+.4f}%")

    # 3) дрейф и размах по часам МСК
    print("\n   по часам МСК (горизонт 1 бар): средний возврат, размах, доля ходов >= cost")
    byh: dict[int, list[float]] = defaultdict(list)
    uph: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        h1 = r.get("horizons", {}).get("1")
        if not h1:
            continue
        hh = msks(int(r["bar_time"]))
        byh[hh].append(float(h1["future_return"]))
        uph[hh].append(max(float(h1["max_up"]), -float(h1["max_down"])))
    good = {h: v for h, v in byh.items() if len(v) >= 400}
    worst = sorted(good.items(), key=lambda x: statistics.mean(x[1]))
    for label, sel in (("3 самых отрицательных часа", worst[:3]), ("3 самых положительных часа", worst[-3:])):
        out = []
        for h, v in sel:
            m, se = mean_se(v)
            med = statistics.median(uph[h])
            out.append(f"{h:02d}:00 {m*100:+.4f}%±{se*100:.4f} ход {med*100:.2f}%")
        print(f"      {label:<26}: " + " | ".join(out))
    allmv = [x for v in uph.values() for x in v]
    print(f"      медиана хода за бар по всем {len(allmv)} наблюдениям: "
          f"{statistics.median(allmv)*100:.3f}% = {statistics.median(allmv)/cost:.2f}x от cost")

    # 4) режим
    byr: dict[str, list[float]] = defaultdict(list)
    byr_mv: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        h1 = r.get("horizons", {}).get("1")
        if not h1:
            continue
        reg = r.get("market_regime") or "?"
        byr[reg].append(float(h1["future_return"]))
        byr_mv[reg].append(max(float(h1["max_up"]), -float(h1["max_down"])))
    print("\n   по режиму (1 бар):")
    for reg, v in sorted(byr.items(), key=lambda x: -len(x[1])):
        if len(v) < 400:
            continue
        m, se = mean_se(v)
        mv = statistics.median(byr_mv[reg])
        print(f"      {reg:<20} n={len(v):>6} дрейф {m*100:+.4f}%±{se*100:.4f} "
              f"| медиана хода {mv*100:.2f}% ({mv/cost:.2f}x cost)")


# --------------------------------------------- 1b. достижимость геометрии (большая n)
LEVELS = [round(0.0025 * k, 4) for k in range(1, 15)]  # 0.25% .. 3.50%


def _first_times(bars: list, levels: list[float], up: bool, hmax: int) -> list[int]:
    """Бар (1-based), на котором кумулятивный экстремум впервые >= уровня.

    bars[j] = (r_next, up_next, dn_next); up_next/dn_next отсчитаны от close бара j,
    поэтому ход high'а от стартового close = cum * (1 + up_next) - 1.
    best монотонен, уровни упорядочены по возрастанию -> один проход, O(hmax + L).
    """
    out = [10**9] * len(levels)
    n = min(len(bars), hmax)
    cum = 1.0
    best = 0.0
    k = 0
    for j in range(n):
        r, up_x, dn_x = bars[j]
        best = max(best, cum * (1.0 + up_x) - 1.0) if up else min(best, cum * (1.0 + dn_x) - 1.0)
        while k < len(levels) and (best >= levels[k] if up else best <= -levels[k]):
            out[k] = j + 1
            k += 1
        if k >= len(levels):
            break
        cum *= 1.0 + r
    return out


def first_passage(paths: dict, hmax: int, stride: int = 1):
    """По всем стартовым барам: кто достигнут раньше — тейк u или стоп d."""
    hits: dict[tuple[float, float], list] = {}
    bars_to_take: dict[tuple[float, float], list] = {}
    n_starts = 0
    for series in paths.values():
        for run in series:
            vals = [x[1:] for x in run]
            # старт берём только если впереди полный хвост >= hmax баров: иначе
            # «не тронуто» было бы цензурой, а не свойством рынка
            for i in range(0, len(vals) - hmax, stride):
                tail = vals[i:i + hmax]
                tu = _first_times(tail, LEVELS, True, hmax)
                td = _first_times(tail, LEVELS, False, hmax)
                n_starts += 1
                for a, u in enumerate(LEVELS):
                    for b, d in enumerate(LEVELS):
                        if u <= d:
                            continue
                        h = hits.setdefault((u, d), [0, 0, 0])
                        if tu[a] < td[b]:
                            h[0] += 1
                            bars_to_take.setdefault((u, d), []).append(tu[a])
                        elif tu[a] >= 10**9 and td[b] >= 10**9:
                            h[2] += 1        # за окно не достигнут ни один уровень
                        else:
                            h[1] += 1        # в т.ч. ничья в одном баре -> консервативно в стоп
    return hits, bars_to_take, n_starts


def geometry(paths: dict, hmax: int) -> None:
    print("\n" + "=" * 92)
    print(f"1b) ДОСТИЖИМОСТЬ ГЕОМЕТРИИ по рынку: окно {hmax} баров = {hmax*5} мин, "
          f"касание = исполнение (лимит по уровню), цензуры нет: у каждого старта полный хвост")
    hits, btt, n_starts = first_passage(paths, hmax)
    print(f"   стартов {n_starts}; стоимость цикла: taker-модель (сейчас) take 0.15% / stop 0.30%;"
          " maker-вход + limit-тейк: take 0.04% / stop 0.19%")
    print("\n   стоп    тейк    RR   p(тейк) p(стоп)  не тронуто  медиана баров  E[R] сейчас  E[R] maker  E[R] брутто")
    print("   " + "-" * 100)
    rows = []
    for d in LEVELS:
        for u in LEVELS:
            if u == d:
                continue
            h = hits.get((u, d))
            if not h or sum(h) < 200:
                continue
            n = sum(h)
            pt, ps, pn = h[0] / n, h[1] / n, h[2] / n
            med = statistics.median(btt[(u, d)]) if btt.get((u, d)) else 0
            gross = (pt * u - ps * d) / d            # без издержек вовсе
            er_now = (pt * (u - 0.0015) - ps * (d + 0.0030) - pn * 0.0030) / d
            er_mk = (pt * (u - 0.0004) - ps * (d + 0.0019) - pn * 0.0019) / d
            rows.append((d, u, u / d, pt, ps, pn, med, er_now, er_mk, gross))
    for d, u, rr, pt, ps, pn, med, er_now, er_mk, gross in rows:
        mark = "  ← плюс" if er_now > 0 else ("  ← плюс у maker" if er_mk > 0 else "")
        print(f"   {d*100:>5.2f}% {u*100:>5.2f}% {rr:>5.2f}  {pt*100:>6.1f}% {ps*100:>6.1f}% {pn*100:>8.1f}%"
              f"{med:>12} {er_now:>+11.3f} {er_mk:>+10.3f} {gross:>+10.3f}{mark}")
    if rows:
        bn = max(rows, key=lambda x: x[7])
        bm = max(rows, key=lambda x: x[8])
        print(f"\n   лучшее при ТЕКУЩИХ издержках: стоп {bn[0]*100:.2f}% / тейк {bn[1]*100:.2f}% "
              f"(RR {bn[2]:.2f}, удержание ~{bn[6]*5} мин) -> E[R] {bn[7]:+.3f}")
        print(f"   лучшее при maker-издержках:    стоп {bm[0]*100:.2f}% / тейк {bm[1]*100:.2f}% "
              f"(RR {bm[2]:.2f}, удержание ~{bm[6]*5} мин) -> E[R] {bm[8]:+.3f}")
        pos_now = [r for r in rows if r[7] > 0]
        pos_mk = [r for r in rows if r[8] > 0]
        pos_gross = [r for r in rows if r[9] > 0]
        best_g = max(rows, key=lambda x: x[9])
        print(f"\n   геометрий с E[R] > 0: брутто (без издержек) {len(pos_gross)} из {len(rows)}, "
              f"при maker-издержках {len(pos_mk)}, при текущих {len(pos_now)}")
        print(f"   максимум брутто: стоп {best_g[0]*100:.2f}%/тейк {best_g[1]*100:.2f}% (RR {best_g[2]:.2f}) -> "
              f"E[R] {best_g[9]:+.3f}, т.е. {best_g[9]*best_g[0]*100:.2f}% цены на сделку ДО издержек; "
              f"цикл стоит {0.0030/best_g[0]*100:.0f}% от 1R")


# ------------------------------------------------------------------------ 2. сделки
class Trade:
    def __init__(self, row: dict):
        self.r = float(row.get("r_multiple") or 0.0)
        self.pnl = float(row.get("pnl") or 0.0)
        self.opened_at = int(row.get("opened_at") or 0)
        self.strategy = row.get("strategy") or "?"
        self.direction = row.get("direction") or "?"
        self.tf = row.get("timeframe") or "?"
        self.axes = row.get("regime_axes") or "?"
        self.hold_min = (int(row["closed_at"]) - self.opened_at) / 60000
        self.costs_r = row.get("costs_r")
        self.stop_pct = row.get("stop_pct")
        self.confidence = row.get("confidence")


def halves(tr: list[Trade]) -> tuple[list[Trade], list[Trade]]:
    srt = sorted(tr, key=lambda t: t.opened_at)
    mid = len(srt) // 2
    return srt[:mid], srt[mid:]


def bucket_table(tr: list[Trade], title: str, keyf, order=None, min_n: int = 8) -> None:
    rr: dict[str, list[float]] = defaultdict(list)
    dd: dict[str, list[float]] = defaultdict(list)
    for t in tr:
        k = keyf(t)
        if k is not None:
            rr[str(k)].append(t.r)
            dd[str(k)].append(t.pnl)
    keep = {k: v for k, v in rr.items() if len(v) >= min_n}
    if len(keep) < 2:
        print(f"\n   {title}\n      (недостаточно данных: бакетов с n>={min_n}: {len(keep)})")
        return
    keys = [k for k in (order(keep) if order else sorted(keep, key=lambda x: statistics.mean(rr[x]))) if k in keep]
    print(f"\n   {title}")
    print(f"      {'бакет':<14}{'n':>5}{'meanR':>9}{'медианаR':>10}{'сумма P&L, U':>15}{'средний P&L,U':>15}")
    for k in keys:
        v, p = rr[k], dd[k]
        print(f"      {k:<14}{len(v):>5}{statistics.mean(v):>9.3f}{statistics.median(v):>10.3f}"
              f"{sum(p):>15.2f}{statistics.mean(p):>15.2f}")


def structural(tr: list[Trade]) -> None:
    print("\n" + "=" * 92)
    print("2) СТРУКТУРНЫЕ СРЕЗЫ по сделкам (порог не подбирается): где теряется R")

    def hold_bucket(t: Trade):
        m = t.hold_min
        return "<5 мин" if m < 5 else "5-15" if m < 15 else "15-60" if m < 60 else "1-6 ч" if m < 360 else ">6 ч"

    bucket_table(tr, "по времени удержания", hold_bucket,
                 order=lambda k: [x for x in ["<5 мин", "5-15", "15-60", "1-6 ч", ">6 ч"] if x in k])

    def costs_bucket(t: Trade):
        c = t.costs_r
        if c is None:
            return None
        return "<0.15R" if c < 0.15 else "0.15-0.25R" if c < 0.25 else ">=0.25R"

    bucket_table(tr, "по доле издержек в 1R (поле costs_r есть только у свежих 52 сделок)",
                 costs_bucket, order=lambda k: [x for x in ["<0.15R", "0.15-0.25R", ">=0.25R"] if x in k])

    def stop_bucket(t: Trade):
        s = t.stop_pct
        if not s:
            return None
        p = s * 100
        return "<1%" if p < 1 else "1-2%" if p < 2 else ">=2%"

    bucket_table(tr, "по ширине начального стопа", stop_bucket,
                 order=lambda k: [x for x in ["<1%", "1-2%", ">=2%"] if x in k])

    def tf_b(t: Trade):
        return t.tf

    bucket_table(tr, "по таймфрейму сигнала", tf_b)

    print("\n   оси режима (средний R: с осью против остальных, плюс воспроизводство на половинках):")
    a, b = halves(tr)
    for axn in ("V:VERY_LOW", "V:LOW", "L:DEEP", "L:NORMAL", "T:RANGE", "T:TREND", "T:WEAK"):
        yes = [t.r for t in tr if axn in t.axes]
        no = [t.r for t in tr if axn not in t.axes]
        if len(yes) < 20 or len(no) < 20:
            continue
        d = statistics.mean(yes) - statistics.mean(no)
        per = []
        for part in (a, b):
            y = [t.r for t in part if axn in t.axes]
            n = [t.r for t in part if axn not in t.axes]
            per.append(statistics.mean(y) - statistics.mean(n) if len(y) >= 4 and len(n) >= 4 else None)
        p = perm_pvalue([yes, no])
        stab = all(x is not None and (x > 0) == (d > 0) for x in per)
        print(f"      {axn:<12} n={len(yes):>3} meanR {statistics.mean(yes):+.3f} vs {statistics.mean(no):+.3f} "
              f"| дельта {d:+.3f}R p={p:.3f} | половинки "
              f"{'/'.join('n/a' if x is None else f'{x:+.2f}' for x in per)} "
              f"{'✓ стабильно' if stab and p < 0.05 else ''}")


def learned(tr: list[Trade]) -> None:
    print("\n" + "=" * 92)
    print("3) LEARNED-ФИЛЬТРЫ: только walk-forward (учимся на 1-й половине, тест на 2-й)")
    a, b = halves(tr)
    print(f"   обучение n={len(a)} (ΣR {sum(t.r for t in a):+.1f}) → тест n={len(b)} "
          f"(ΣR {sum(t.r for t in b):+.1f})\n")
    print(f"   {'правило, выведенное на 1-й половине':<46}{'вырезано во 2-й':>16}"
          f"{'ΔR 2-й половины':>17}{'p':>8}  вердикт")
    print("   " + "-" * 100)
    res = []
    specs = (
        ("стратегии с meanR < -0.15 (n>=2)", lambda t: t.strategy, 2, -0.15),
        ("стратегии с meanR < 0 (n>=2)", lambda t: t.strategy, 2, 0.0),
        ("часы МСК с meanR < 0 (n>=2)", lambda t: str(msks(t.opened_at)), 2, 0.0),
        ("часы МСК с meanR < -0.2 (n>=2)", lambda t: str(msks(t.opened_at)), 2, -0.2),
        ("ТФ с meanR < -0.15", lambda t: t.tf, 2, -0.15),
        ("направление с meanR < -0.15", lambda t: t.direction, 2, -0.15),
        ("сделки <5 мин удержания (правило фиксировано)", lambda t: "<5" if t.hold_min < 5 else ">=5", 2, 0.0),
        ("ось L:DEEP (правило фиксировано)", lambda t: "DEEP" if "L:DEEP" in t.axes else "other", 2, 0.0),
    )
    for label, keyf, min_n, thr in specs:
        stats: dict = defaultdict(list)
        for t in a:
            stats[keyf(t)].append(t.r)
        bad = {k for k, v in stats.items() if len(v) >= min_n and statistics.mean(v) < thr}
        if not bad:
            continue
        cut = [t for t in b if keyf(t) in bad]
        kept = [t for t in b if keyf(t) not in bad]
        if len(cut) < 3 or len(kept) < 10:
            continue
        d = sum(t.r for t in kept) - sum(t.r for t in b)
        p = perm_pvalue([[t.r for t in kept], [t.r for t in cut]])
        verdict = "✓ подтверждено out-of-sample" if (d > 0 and p < 0.10) else "✗ не подтверждено"
        res.append((d, label, len(cut), p, verdict))
    for d, label, ncut, p, verdict in sorted(res, key=lambda x: -x[0]):
        print(f"   {label:<46}{ncut:>16}{d:>+17.1f}{p:>8.3f}  {verdict}")
    if not res:
        print("   (ни одно правило не дало достаточной поддержки)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.003)
    ap.add_argument("--trades", default=DEFAULT_TRADES)
    ap.add_argument("--hmax", type=int, default=24, help="горизонт поиска, баров (24 = 2ч)")
    ap.add_argument("--no-geometry", action="store_true")
    ap.add_argument("--outcomes", default=DEFAULT_OUTCOMIES)
    args = ap.parse_args()
    opath = Path(args.outcomes)
    if opath.exists():
        rows = list(json.loads(opath.read_text(encoding="utf-8")).get("outcomes", {}).values())
        paths = build_paths(rows)
        excursions(paths, args.cost)
        microstructure(paths, rows, args.cost)
        if not args.no_geometry:
            geometry(paths, args.hmax)
    else:
        print(f"нет {opath} — пропуск блока структуры рынка")
    tr = [Trade(r) for r in
          (json.loads(l) for l in Path(args.trades).read_text(encoding="utf-8").splitlines() if l.strip())
          if r.get("closed_at") and r.get("r_multiple") is not None]
    print(f"\nзакрытых сделок: {len(tr)}, ΣR {sum(t.r for t in tr):+.1f}, "
          f"P&L {sum(t.pnl for t in tr):+.2f} U, средний R {statistics.mean(t.r for t in tr):+.3f}")
    structural(tr)
    learned(tr)
    print("\n" + "=" * 92)
    print("МОЩНОСТЬ: 184 сделки при σ(R)≈0.6 не дают значимости даже честному фильтру на")
    print("0.1R/сделку; поэтому learned-правила оценены только walk-forward, а выводы о")
    print("масштабе берутся из блока 1 (десятки тысяч баров) — он про горизонт и издержки.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
