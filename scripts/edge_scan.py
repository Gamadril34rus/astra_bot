#!/usr/bin/env python3
"""Edge scan: есть ли вообще край, который делает сделки плюсовыми (read-only).

Вопрос, на который отвечает скрипт: «привести бота в плюсовые сделки» — что для
эгого нужно и существует ли это в данных. Ответ строится из четырёх блоков:

  A. Брутто-край на 5m-панели (31 символ × 1068 баров) — сколько рынок вообще
     даёт предиктивности: автокорреляция, momentum/reversal, BTC-лид, кросс-
     секционный моментум.
  B. Полная симуляция сделки с моделью исполнения: вход лимитом на close
     сигнального бара (исполняется, только если следующий бар торговался на этом
     уровне), тейк — maker по касанию, стоп — taker (+ два варианта цены стопа:
     по экстремуму бара и «ровно уровень + слип»), выход по времени — маркет.
     Ничья тейк/стоп в одном баре засчитана в стоп (консервативно).
  C. Гейты, которые можно вычислить в момент решения: волатильность на входе
     (медиана |r| за 24 пред. бара) против достижимого хода, и те же гейты на
     собственном логе сделок.
  D. Потолок политики выходов: сколько в принципе даёт «идеальный выход» по
     mfe_r из лога, и сколько стоит цикл издержек.

Данные: только репозиторий (`models/no_trade_outcomes.json`, `models/paper_trades
.jsonl`); внешний HTTP недоступен, живых стаканов нет, поэтому исполнение
моделируется, а не наблюдается. Отсюда главное методологическое предупреждение:
правка fee/slippage в paper-модели меняет табло, а не торговлю.

Запуск: python3 scripts/edge_scan.py
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lever_scan import build_paths

SRC = ROOT / "models" / "no_trade_outcomes.json"
TRADES = ROOT / "models" / "paper_trades.jsonl"
BTC = "BTC-USDT"

MAKER = 0.0002          # 2 б.п. — лимитка (BingX maker)
TAKER = 0.0005          # 5 б.п. — маркет
SLIP = 0.001            # 10 б.п. — проскальзывание маркет-ордера
STOP_SLIP = 0.0005      # 5 б.п. — «уровень + слип» в оптимистичном варианте стопа
BAR_MS = 300_000


# ---------------------------------------------------------------- панель ------
class Panel:
    """Матрица [символ][бар] = (close-to-close r, max up, max down) без дырок."""

    def __init__(self) -> None:
        with open(SRC, encoding="utf-8") as fh:
            rows = list(json.load(fh)["outcomes"].values())
        paths = build_paths(rows)
        cell: dict[tuple[str, int], tuple[float, float, float]] = {}
        for sym, runs in paths.items():
            for run in runs:
                for ts, r, u, d in run:
                    cell[(sym, ts)] = (r, u, d)
        self.ts = sorted({t for _s, t in cell})
        idx = self.idx = {t: i for i, t in enumerate(self.ts)}
        self.syms = sorted(paths)
        self.n = len(self.ts)
        self.r: dict[str, list[float | None]] = {s: [None] * self.n for s in self.syms}
        self.u: dict[str, list[float | None]] = {s: [None] * self.n for s in self.syms}
        self.d: dict[str, list[float | None]] = {s: [None] * self.n for s in self.syms}
        for (s, t), (r, u, d) in cell.items():
            i = idx[t]
            self.r[s][i], self.u[s][i], self.d[s][i] = r, u, d
        self.alts = [s for s in self.syms if s != BTC]
        # vol[symbol][bar_ts] = медиана |r| за 24 предыдущих бара (известна в момент решения)
        self.vol: dict[str, dict[int, float]] = {}
        for sym, runs in paths.items():
            for run in runs:
                vals = [(x[0], x[1]) for x in run]
                for k in range(24, len(vals)):
                    self.vol.setdefault(sym, {})[vals[k][0]] = statistics.median(
                        [abs(v) for _t, v in vals[k - 24:k]]
                    )

    def utc(self, i: int) -> datetime:
        return datetime.fromtimestamp(self.ts[i] / 1000, tz=UTC)


# ------------------------------------------------------------------- метрики ---
def mean_se(vals: list[float]) -> tuple[float, float, float]:
    """(среднее, ст.ошибка, t) — в тех же единицах, что и вход."""
    if len(vals) < 2:
        return (float("nan"),) * 3  # type: ignore[assignment]
    m = statistics.mean(vals)
    se = statistics.pstdev(vals) / math.sqrt(len(vals))
    return m, se, (m / se if se else float("nan"))


def fmt_bps(vals: list[float], scale: float = 10_000) -> str:
    m, se, t = mean_se(vals)
    if len(vals) < 30:
        return f"n={len(vals):>5}  (мало)"
    return (f"{m * scale:>+7.2f}±{se * scale:4.2f} (t={t:+5.1f}) n={len(vals):>5}")


# --------------------------------------------------------- A. брутто-предиктивность
def block_gross(p: Panel) -> None:
    print("=== A. сколько рынок даёт предиктивности (брутто, без издержек) ===")
    ac1 = []
    for s in p.syms:
        col = p.r[s]
        for i in range(p.n - 6):
            a, b = col[i], col[i + 1]
            if a is None or b is None:
                continue
            ac1.append((a, b))
    x = [a for a, _b in ac1]
    y = [b for _a, b in ac1]
    sx = statistics.pstdev(x)
    sy = statistics.pstdev(y)
    # covariance() уже делит на (n-1); σ — pop-оценки, поэтому множитель (n-1)/n
    rho = statistics.covariance(x, y) * (len(x) - 1) / len(x) / sx / sy if sx and sy else float("nan")
    t_rho = rho * math.sqrt((len(x) - 2) / max(1e-12, 1 - rho * rho))
    print("  (ниже вход = close того же бара, что породил сигнал — идеализация;")
    print("   что от неё остаётся при реальных заявках — блок B)")
    print(f"  автокорреляция 5m lag1 (последовательные непрекрывающиеся бары): "
          f"ρ={rho:+.4f} (t={t_rho:+.1f}, n={len(x)}) — отрицательная = реверс")

    print("\n  1) фейд собственного бара символа (|r|>порог → против):")
    for th in (0.0010, 0.0020, 0.0030):
        for h in (1, 3, 6):
            vals = []
            for s in p.syms:
                col = p.r[s]
                for i in range(p.n - h - 1):
                    a = col[i]
                    if a is None or abs(a) < th:
                        continue
                    cum = 1.0
                    for j in range(i + 1, i + 1 + h):
                        if col[j] is None:
                            break
                        cum *= 1.0 + col[j]
                    vals.append(-(cum - 1.0) * (1 if a > 0 else -1))
            print(f"     |r|>{th * 100:.2f}%  h={h}: {fmt_bps(vals)} б.п.")

    print("\n  2) фейд бара BTC на альтах (лид-реверс):")
    for th in (0.0015, 0.0020, 0.0030):
        for h in (1, 3, 6):
            vals = []
            for i in range(p.n - h - 1):
                b = p.r[BTC][i]
                if b is None or abs(b) < th:
                    continue
                dirn = -1 if b > 0 else 1
                for s in p.alts:
                    cum = 1.0
                    ok = False
                    for j in range(i + 1, i + 1 + h):
                        if p.r[s][j] is None:
                            break
                        cum *= 1.0 + p.r[s][j]
                        ok = True
                    if ok:
                        vals.append(dirn * (cum - 1.0))
            print(f"     |r_BTC|>{th * 100:.2f}%  h={h}: {fmt_bps(vals)} б.п.")

    print("\n  3) кросс-секционный моментум/реверс (long top-k, short bottom-k):")
    any_sig = False
    for look in (3, 12):
        for h in (3, 12):
            for k in (3, 6):
                vals = []
                for i in range(p.n - look - h - 1):
                    rel = []
                    for s in p.alts:
                        a = p.r[s][i]
                        if a is None:
                            continue
                        cum = 1.0
                        for j in range(i - look + 1, i + 1):
                            if p.r[s][j] is None:
                                break
                            cum *= 1.0 + p.r[s][j]
                        rel.append((cum - 1.0, s))
                    if len(rel) < 2 * k:
                        continue
                    rel.sort(reverse=True)
                    top = [x[1] for x in rel[:k]]
                    bot = [x[1] for x in rel[-k:]]
                    for grp, sign in ((top, 1.0), (bot, -1.0)):
                        for s in grp:
                            cum = 1.0
                            for j in range(i + 1, i + 1 + h):
                                if p.r[s][j] is None:
                                    break
                                cum *= 1.0 + p.r[s][j]
                            vals.append(sign * (cum - 1.0))
                if len(vals) >= 30:
                    any_sig = True
                    print(f"     look={look:>2} h={h:>2} k={k}: {fmt_bps(vals)} б.п.")
    if not any_sig:
        print("     (недостаточно наблюдений)")
    print("     → после двух ног × 15 б.п. это −30..−60 б.п.: направление закрыто")


# --------------------------------------------------------- B. исполнение
def simulate(
    p: Panel,
    *,
    signal: str,
    theta: float,
    theta_alt: float,
    take: float,
    stop: float | None,
    hold: int,
    fee: str,
    stop_fill: str = "extreme",
) -> list[tuple[int, str, float, str]]:
    """Одна сделка целиком, с ЧЕСТНЫМ выравниванием по времени.

    Индексы панели: r[s][t] — доход close_t→close_{t+1}; u/d[s][t] — экстремумы
    бара t+1 относительно close_t. Решение принимается на close_i, поэтому
    направление берётся из ЗАВЕРШЁННОГО бара i−1 (r[s][i-1]), а не из r[s][i].
    Вход — лимит на close_i; исполняется, если бар i+1 торговался на уровне или
    лучше (u/d с индексом i). Проверка выхода — с бара i+2 (порядок внутри бара
    i+1 после нашего филла неизвестен, поэтому там выходов не считаем).
    net — доля от нотионала уже за вычетом издержек.
    """
    entry_cost = MAKER if fee == "maker" else TAKER + SLIP
    out: list[tuple[int, str, float, str]] = []
    for i in range(1, p.n - hold - 2):
        b = p.r[BTC][i - 1]
        if b is None or abs(b) < theta:
            continue
        for s in p.alts:
            r_sig = p.r[s][i - 1]
            if r_sig is None:
                continue
            if signal == "btc_lead":
                dirn = -1 if b > 0 else 1
            elif signal == "self":
                if abs(r_sig) < theta_alt:
                    continue
                dirn = -1 if r_sig > 0 else 1
            else:  # combo: импульс символа в ту же сторону, что и BTC → фейдим подтверждённый перелёт
                if abs(r_sig) < theta_alt or r_sig * b <= 0:
                    continue
                dirn = -1 if r_sig > 0 else 1
            u_ent, d_ent = p.u[s][i], p.d[s][i]
            if u_ent is None or d_ent is None:
                continue
            filled = (d_ent <= 0) if dirn > 0 else (u_ent >= 0)
            if fee == "taker":
                filled = True
            if not filled:
                continue
            # цена = 1.0 на close_i; после бара i+1 (её закрытие) — 1 + r[s][i]
            cum = 1.0 + p.r[s][i]
            kind: str | None = None
            px: float | None = None
            for k in range(i + 1, i + 1 + hold):
                if p.r[s][k] is None:
                    break
                uk = p.u[s][k] or 0.0
                dk = p.d[s][k] or 0.0
                hi = cum * (1.0 + uk) - 1.0
                lo = cum * (1.0 + dk) - 1.0
                if dirn > 0:
                    if stop is not None and lo <= -stop:      # ничья → стоп (консервативно)
                        px = min(lo, -stop) if stop_fill == "extreme" else -(stop + STOP_SLIP)
                        kind = "stop"
                    elif hi >= take:
                        kind, px = "take", take
                else:
                    if stop is not None and hi >= stop:
                        px = max(hi, stop) if stop_fill == "extreme" else stop + STOP_SLIP
                        kind = "stop"
                    elif lo <= -take:
                        kind, px = "take", -take
                if kind is not None:
                    break
                cum *= 1.0 + p.r[s][k]
            if kind is None:
                kind, px = "time", cum - 1.0
            gross = dirn * px
            if kind == "take" and fee == "maker":
                exit_cost = MAKER
            elif fee == "maker":
                exit_cost = TAKER + STOP_SLIP        # стоп/тайм-выход маркетом
            else:
                exit_cost = TAKER + SLIP
            out.append((i, s, gross - entry_cost - exit_cost, kind))
    return out


def block_execution(p: Panel) -> None:
    print("\n=== B. сделка целиком: лимитный вход + тейк-maker + стоп-taker ===")
    print(f"{'сигнал':>9} {'порог':>6} {'тейк':>5} {'стоп':>5} {'дер':>4} {'сбор':>6} {'стоп-филл':>10} "
          f"{'нетто/сд':>9} {'t':>6} {'n':>6} {'p(тейк)':>8} {'худшая':>8}")
    grid = [
        ("btc_lead", 0.0015, 0.0), ("btc_lead", 0.0010, 0.0),
        ("self", 0.0, 0.0030), ("combo", 0.0010, 0.0010),
    ]
    combos = [(0.0010, None, 2), (0.0020, None, 3), (0.0030, None, 6), (0.0020, 0.0015, 3),
              (0.0030, 0.0020, 6), (0.0020, 0.0050, 6), (0.0030, 0.0050, 6)]
    rows = []
    for sig, th, tha in grid:
        for tk, sl, hold in combos:
            for fee in ("maker", "taker"):
                for sf in (("extreme", "level") if sl is not None else ("extreme",)):
                    t = simulate(p, signal=sig, theta=th, theta_alt=tha,
                                 take=tk, stop=sl, hold=hold, fee=fee, stop_fill=sf)
                    if len(t) < 200:
                        continue
                    v = [x[2] for x in t]
                    m, _se, tt = mean_se(v)
                    kinds = collections.Counter(x[3] for x in t)
                    print(f"{sig:>9} {max(th, tha) * 100:>5.2f}% {tk * 10000:>4.0f}б "
                          f"{(str(round(sl * 10000)) + 'б') if sl else '  нет':>5} {hold:>4} {fee:>6} {sf:>10} "
                          f"{m * 10000:>+7.2f} {tt:>+6.1f} {len(v):>6} {100 * kinds['take'] / len(t):>7.1f}% "
                          f"{min(v) * 10000:>+7.1f}")
                    rows.append((tt, sig, th, tha, tk, sl, hold, fee, sf, t))
    rows.sort(reverse=True, key=lambda x: x[0])
    if rows:
        tt, sig, th, tha, tk, sl, hold, fee, sf, t = rows[0]
        mid = p.n // 2
        e = [x[2] for x in t if x[0] < mid]
        l = [x[2] for x in t if x[0] >= mid]
        print(f"\n  лучший по t: {sig} θ={max(th, tha) * 100:.2f}% tk={tk * 10000:.0f}б "
              f"sl={sl if sl else 'нет'} h={hold} fee={fee} стоп-филл={sf}")
        print(f"    весь период {fmt_bps([x[2] for x in t])} | 1-я половина {fmt_bps(e)} | "
              f"2-я половина {fmt_bps(l)}")
        bars = len({x[0] for x in t})
        print(f"    частота: {len(t)} сделок на {bars} барах = {len(t) / max(1, bars):.1f} сделок/бар, "
              f"{len(t) / 6.71:.0f} сделок/сутки")
        print("    (не перемножать это на экспозицию: 30 одновременных позиций —"
              " иллюзия, маржа и лимит позиций её не дадут)")


# --------------------------------------------------------- C. гейты
def block_gates(p: Panel) -> None:
    print("\n=== C. гейт по волатильности на входе ===")
    print("  C1. рынок: vol (медиана |r| за 24 пред. бара) против достижимого хода за 24 бара")
    recs = []
    for sym, d in p.vol.items():
        for t, v in d.items():
            i = p.idx.get(t)
            if i is None or i + 25 >= p.n:
                continue
            cum = 1.0
            hi = lo = 0.0
            for j in range(i + 1, i + 25):
                if p.r[sym][j] is None:
                    break
                cum *= 1.0 + p.r[sym][j]
                hi = max(hi, cum * (1.0 + (p.u[sym][j] or 0.0)) - 1.0)
                lo = min(lo, cum * (1.0 + (p.d[sym][j] or 0.0)) - 1.0)
            recs.append((v, hi, lo))
    recs.sort()
    N = len(recs)
    print(f"    {'квантиль vol':>12} {'медиана vol':>12} {'ход 2ч от close':>18} {'доля: лучшая сторона':>22}")
    print(f"    {'(входной)':>12} {'':>12} {'верх / низ':>18} {'оплатила 0.30%':>22}")
    for a, b, lab in ((0.0, 0.2, "0-20%"), (0.2, 0.4, "20-40%"), (0.4, 0.6, "40-60%"),
                      (0.6, 0.8, "60-80%"), (0.8, 1.0, "80-100%")):
        sub = recs[int(N * a):int(N * b)]
        if len(sub) < 50:
            continue
        vols = [x[0] for x in sub]
        hi = [x[1] for x in sub]
        lo = [x[2] for x in sub]
        best = [max(a, -b) for a, b in zip(hi, lo, strict=False)]
        print(f"    {lab:>12} {statistics.median(vols) * 100:>11.3f}% "
              f"{statistics.median(hi) * 100:>8.3f}%/{statistics.median(lo) * 100:+7.3f}% "
              f"{100 * sum(1 for e in best if e >= 0.003) / len(best):>26.1f}%")
    print("    → в нижних 40% квантилей достижимый ход В 2-3 раза меньше цены цикла;")
    print("      в верхних 20% — сопоставим или больше. Казалось бы, «торговать только в шуме».")

    print("\n  C2. собственные сделки бота: meanR по квантилям vol на входе")
    with open(TRADES, encoding="utf-8") as fh:
        tr = [json.loads(line) for line in fh if line.strip()]
    tr = [t for t in tr if t.get("closed_at") and t.get("r_multiple") is not None]
    pairs = []
    missed = 0
    for t in tr:
        d = p.vol.get(t["symbol"]) or {}
        cand = [ts_ for ts_ in d if ts_ <= t["opened_at"] + 60_000]
        if not cand:
            missed += 1
            continue
        pairs.append((d[max(cand)], t["r_multiple"], t["pnl"]))
    if len(pairs) >= 40:
        pairs.sort()
        M = len(pairs)
        print(f"    покрытие: {M} из {len(tr)} сделок (нет 24-барной истории: {missed})")
        for a, b, lab in ((0.0, 0.2, "0-20%"), (0.2, 0.4, "20-40%"), (0.4, 0.6, "40-60%"),
                          (0.6, 0.8, "60-80%"), (0.8, 1.0, "80-100%")):
            sub = pairs[int(M * a):int(M * b)]
            r = [x[1] for x in sub]
            print(f"    {lab:>12} n={len(sub):>3}  meanR {statistics.mean(r):+.3f}  "
                  f"P&L {sum(x[2] for x in sub):+8.2f} U")
        med = statistics.median([x[0] for x in pairs])
        hi = [x[1] for x in pairs if x[0] >= med]
        lo = [x[1] for x in pairs if x[0] < med]
        pooled = math.sqrt(statistics.pvariance(hi) / len(hi) + statistics.pvariance(lo) / len(lo))
        delta = statistics.mean(hi) - statistics.mean(lo)
        print(f"    сплит по медиане vol ({med * 100:.3f}%): vol≥мед {statistics.mean(hi):+.3f} (n={len(hi)}) "
              f"vs vol<мед {statistics.mean(lo):+.3f} (n={len(lo)}) → Δ {delta:+.3f}R (t={delta / pooled:+.2f})")
        random_vals = [x[1] for x in pairs]
        random.seed(20260914)
        cnt = 0
        trials = 4000
        for _ in range(trials):
            random.shuffle(random_vals)
            if statistics.mean(random_vals[:len(hi)]) - statistics.mean(random_vals[len(hi):]) >= delta:
                cnt += 1
        print(f"    p(перестановочный, {trials}) = {min(cnt, trials - cnt) / trials * 2:.3f} (двусторонний)")
        print("    → знак ПРОТИВОПОЛОЖНЫЙ гипотезе: чем громче рынок на входе, тем хуже сделка.")
        print("      «заходить только в высокой vol» нельзя; «заходить только в тишине» —")
        print("      meanR всё равно −0.26R, а ход в тишине не оплачивает цикл (см. C1).")


# --------------------------------------------------------- D. лог: потолок и издержки
def block_log(p: Panel) -> None:
    print("\n=== D. лог сделок: откуда берётся минус и каков потолок улучшений ===")
    with open(TRADES, encoding="utf-8") as fh:
        tr = [json.loads(line) for line in fh if line.strip()]
    tr = [t for t in tr if t.get("closed_at") and t.get("exit_price") is not None]
    mv = []
    notional = 0.0
    for t in tr:
        sgn = 1 if t.get("direction") == "long" else -1
        mv.append(((t["exit_price"] - t["entry_price"]) / t["entry_price"]) * sgn)
        notional += t["entry_price"] * t["quantity"]
    fees = sum(t.get("fees") or 0.0 for t in tr)
    fund = sum(t.get("funding") or 0.0 for t in tr)
    net = sum(t["pnl"] for t in tr)
    price = sum(m * t["entry_price"] * t["quantity"] for m, t in zip(mv, tr, strict=False))
    cycle = 2 * (TAKER + SLIP)
    # Σpnl = ход цены − слип(2 стороны) − комиссии + фандинг → слип выводим из равенства
    slip_total = -(net - (price - fees + fund))
    costs = slip_total + fees
    print(f"  сделок {len(tr)}, Σpnl {net:+.2f} U, ΣR {sum(t['r_multiple'] for t in tr):+.2f}")
    print(f"  ход цены по сигнальным ценам : {price:+9.2f} U  ({statistics.mean(mv) * 10000:+.1f} б.п./сделку)")
    print(f"  слиппедж (2 стороны, вывод)  : {-slip_total:+9.2f} U")
    print(f"  комиссии (2 стороны)          : {-fees:+9.2f} U")
    print(f"  фандинг                       : {fund:+9.2f} U")
    print(f"  → издержки {costs:.2f} U = {costs / abs(net) * 100:.0f}% убытка; "
          f"цена цикла {cycle * 100:.2f}% от нотионала ({cycle * 10000:.1f} б.п.)")
    print(f"  чтобы быть в плюсе, средний грязто-ход должен быть > {cycle * 10000:.1f} б.п.; "
          f"сейчас {statistics.mean(mv) * 10000:+.1f} б.п. при σ {statistics.pstdev(mv) * 10000:.0f} б.п.")

    good = []
    for t in tr:
        s = t.get("initial_stop")
        m = t.get("mfe_r")
        if not s or m is None:
            continue
        risk = abs(t["entry_price"] - s) * t["quantity"]
        nt = t["entry_price"] * t["quantity"]
        if 0 < risk <= 0.05 * nt:
            good.append((t, risk))
    if len(good) >= 30:
        act = sum(t["pnl"] for t, _ in good)
        oracle = sum(t["mfe_r"] * r for t, r in good)
        nt = sum(t["entry_price"] * t["quantity"] for t, _ in good)
        print("\n  потолок политики ВЫХОДОВ (оракульный выход на вершине каждой сделки),")
        print(f"  на {len(good)} сделках с полными полями (у остальных нет initial_stop):")
        print(f"    факт {act:+.2f} U → оракул {oracle:+.2f} U, запас {oracle - act:+.2f} U "
              f"({(oracle - act) / len(good):+.2f} U/сделку)")
        print(f"    медиана mfe_r {statistics.median(t['mfe_r'] for t, _ in good):.3f}R = "
              f"{statistics.median(t['mfe_r'] for t, _ in good) * statistics.median(r for _t, r in good):.2f} U "
              f"против цикла {nt * cycle / len(good):.2f} U на сделку")
        print("    → даже зная вершину каждой сделки (lookahead, недоступно в жизни), выход")
        print("      даёт меньше, чем стоит цикл. Улучшать выход — не за счёт чего.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="A|B|C|D — только один блок")
    args = ap.parse_args()
    p = Panel()
    print(f"панель: {p.n} баров × {len(p.syms)} символов, "
          f"{p.utc(0):%m-%d %H:%M} → {p.utc(p.n - 1):%m-%d %H:%M} UTC")
    only = args.only.upper()
    if only in ("", "A"):
        block_gross(p)
    if only in ("", "B"):
        block_execution(p)
    if only in ("", "C"):
        block_gates(p)
    if only in ("", "D"):
        block_log(p)
    print("\nОГРАНИЧЕНИЯ, которые нужно держать в голове при чтении любого «плюса» выше:")
    print("  1. 6.5 суток одного режима рынка (LOW_VOLATILITY в 99.7% баров) — годовой")
    print("     «шарп» из 5 дней не имеет смысла, это только знак и порядок величины.")
    print("  2. Исполнение смоделировано, а не наблюдалось: live-путь отключён")
    print("     (BingXClient.place_order → NotImplementedError), стакан недоступен,")
    print("     поэтому maker-фили и гэпы стопа — предположение, а не факт.")
    print("  3. Пороги/гейты, выбранные по этим же 180 сделкам, оптимистичны: при 8-10")
    print("     проверенных осях отбор даёт ложный +0.2..0.3R сам по себе.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
