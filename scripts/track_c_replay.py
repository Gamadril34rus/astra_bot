#!/usr/bin/env python3
"""Track C replay — суд урокам Зевса на исторических данных (PR #77).

Read-only по ``models/``: скрипт читает ТОЛЬКО CSV из ``--data-dir`` (клон
внешнего архива) и ничего не пишет в репозиторий. Правила гипотез и
пороги зафиксированы КОНСТАНТАМИ ДО прогона и не подбираются по OOS:
подгонка порога под OOS = провал задания (методика PR #77 §3).

Схема:
  * данные — Binance-labelled CSV (timestamp,open,high,low,close,volume),
    1h ~41.6 дня, 4h ~166 дней; provenance — строка ``--provenance``;
  * 70% окна (по времени) — отбор, 30% — ОДНА финальная OOS-проверка;
  * издержки 0.3% round-trip (0.15% на сторону) включены в каждый расчёт R
    (и во вход, и в выход);
  * против каждой гипотезы — наша боевая версия той же фигуры (вход
    пробоем закрытия; геометрия/пороги зеркалят
    ``astra_bot/decision/strategies/pattern_strategies.py``) на том же окне;
  * метрики каждой гипотезы И базлайна: n, PF, meanR, medianR, maxDD в R,
    доля сделок с MFE>=1R, bootstrap 95% CI meanR (seed фиксирован,
    20000 resamples);
  * одна позиция = один sample (частичек в реплее нет: одиночный тейк).

Запуск:
    python scripts/track_c_replay.py \\
        --data-dir /tmp/zeus_data \\
        --provenance "github.com/eltonaguiar/findtorontoevents_antigravity.ca-archive-2026-05-23 @ 6beb4511"
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED = 20260913
RESAMPLES = 20_000
SYMBOLS = ("BTC", "ETH", "SOL")
TIMEFRAMES = ("1h", "4h")
SPLIT = 0.70  # доля окна на отбор (по времени), остаток — ОДИН OOS-срез

# ---------------------------------------------------------------------------
# Фиксированные правила (НЕ тюнятся; зеркалят pattern_strategies.py)
# ---------------------------------------------------------------------------
LEVEL_TOL = 0.004  # 0.4% — равенство двух/трёх экстремумов (pattern_strategies.py:215)
MIN_HEIGHT_PCT = 0.003  # 0.3% цены — минимум высоты фигуры (pattern_strategies.py:221)
SWING_W = 2  # окно свингов double/triple (pattern_strategies.py:203)
HS_SWING_W = 3  # окно свингов ГП (pattern_strategies.py:919)
PLATEAU_GAP = 3  # схлопывание плато свингов (pattern_strategies.py:169)
NECK_GAP = 0.002  # 0.2% — зазор пробоя шеи double/triple (pattern_strategies.py:227)
HS_NECK_GAP = 0.0  # ГП: пробой — просто закрытие за шеей (pattern_strategies.py:963)
CHANNEL_NECK_GAP = 0.0  # прямоугольник: закрытие за границей без зазора (pattern_strategies.py:448)
STOP_BUFFER = 0.001  # 0.1% — буфер стопа за экстремум (pattern_strategies.py:1715)
TARGET_MIN_RR = 1.6  # минимум RR цели от риска (pattern_strategies.py:1717)
COST_PER_SIDE = 0.0015  # 0.15% на сторону (fee+slippage) = 0.3% round-trip
MIN_RISK_PCT = 0.003  # стоп уже round-trip издержек (0.3%) = сделка неиграбельна, пропуск
MAX_HOLD_BARS = 60  # тайм-стоп позиции (фикс.)
BREAKOUT_MAX_BARS = 20  # пробой не позже N баров после последнего свинга (свежесть)
RETEST_TOL = 0.003  # Z1: зона касания шеи при ретесте
CH_TOUCH_TOL = 0.003  # Z3/Z5: допуск касания границы
OVERSHOOT_GAP = 0.001  # Z4: перехай уровня на 0.1% с закрытием обратно
RSI_PERIOD = 14  # Z2: период RSI (урок 9 автора; файл в origin/arena-01a08cde)


# ---------------------------------------------------------------------------
# PR #78 (часть 2): официальные архивы Binance, HTF-контекст и Z6/Z7.
# Правила зафиксированы ДО прогона; подгонка под OOS = провал задания.
# ---------------------------------------------------------------------------
# Z6: ретест-вход разворота РАЗРЕШЁН только при согласии дневной ленты EMA
# 20/50/100/200 (то же правило, что в боевом astra_bot/decision/htf_gates.py:
# бычий стэк + цена над EMA20 → лонг; зеркально → шорт; neutral/None → отказ).
Z6_STRICT_CONTEXT = True          # нет контекста / нейтральная лента = сигнала нет
RIBBON_MIN_DAILY_BARS = 200       # как htf_gate_min_bars в проде
RIBBON_EMAS = (20, 50, 100, 200)
DAY_SECONDS = 86_400

# Z7: вход у границы зоны только ПОСЛЕ реакции — первое закрытие «в сторону
# входа» после касания границы (зелёная свеча, закрывшаяся внутри зоны для
# лонга; зеркально для шорта). Касание — с допуском CH_TOUCH_TOL.
Z7_MIN_TOUCHES = 2                # не торгуем первое касание свежего уровня
Z7_REQUIRE_INSIDE = True          # закрытие должно вернуться за границу зоны
Z7_REACTION_BARS = 6              # реакция должна наступить в этом окне после касания

# Метки силы. strong НЕ присваивается НИКОГДА (решение PR #77: много гипотез на
# одних данных). Пороги фиксированы, OOS-окно единственное.
VERDICT_MODERATE_MIN_N = 100
VERDICT_MODERATE_MIN_CI_LOW = 0.0
VERDICT_WEAK_MIN_N = 30

BINANCE_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore",
]


def _ema_last(values: list[float], period: int) -> float | None:
    """EMA по образцу astra_bot.core.utils.exponential_moving_average (посев от values[0])."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = float(values[0])
    for v in values[1:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


try:  # боевая реализация — тот же код, что режет входы в проде
    from astra_bot.decision.htf_gates import ribbon_bias as _ribbon_bias_prod  # type: ignore

    RIBBON_SOURCE = "astra_bot.decision.htf_gates.ribbon_bias"
except Exception:  # pragma: no cover — скрипт должен работать и без тяжёлых зависимостей
    _ribbon_bias_prod = None
    RIBBON_SOURCE = "local replica"


def ribbon_bias(closes: list[float], min_bars: int = RIBBON_MIN_DAILY_BARS) -> str | None:
    """Бычий/медвежий/нейтральный стэк дневной ленты; None — контекст недоступен."""
    if _ribbon_bias_prod is not None:
        bias, _diag = _ribbon_bias_prod(closes, min_bars=min_bars)
        return bias
    if len(closes) < min_bars:
        return None
    emas = [_ema_last(closes, p) for p in RIBBON_EMAS]
    if any(e is None for e in emas):
        return None
    e20, e50, e100, e200 = (float(e) for e in emas)
    last = float(closes[-1])
    if e20 > e50 > e100 > e200 and last > e20:
        return "bull"
    if e20 < e50 < e100 < e200 and last < e20:
        return "bear"
    return "neutral"


def daily_bias(bars: list[dict[str, Any]]) -> list[str | None]:
    """Смещение на КАЖДЫЙ бар — по ленте из закрытых дневных баров ПРЕДЫДУЩИХ дней.

    Текущий (незавершённый) день не используется: иначе HTF-фильтр подглядывал
    бы в будущее. Это то же требование, что у боевого htf_gates (min_closed_bars).
    """
    bias: list[str | None] = [None] * len(bars)
    days: list[int] = []
    day_close: list[float] = []
    by_day: dict[int, int] = {}
    for b in bars:
        d = int(b["ts"]) // DAY_SECONDS
        if d in by_day:
            day_close[by_day[d]] = float(b["close"])
        else:
            by_day[d] = len(days)
            days.append(d)
            day_close.append(float(b["close"]))
    # префиксные исходы: на день d доступна лента по дням 0..d-1
    prefix_bias: dict[int, str | None] = {}
    for i, d in enumerate(days):
        closes = day_close[:i]  # только ЗАКРЫТЫЕ дни
        prefix_bias[d] = ribbon_bias(closes) if len(closes) >= RIBBON_MIN_DAILY_BARS else (
            ribbon_bias(closes) if closes else None
        )
    for b_idx, b in enumerate(bars):
        d = int(b["ts"]) // DAY_SECONDS
        bias[b_idx] = prefix_bias.get(d)
    return bias


def _to_seconds(value: object) -> int:
    """open_time из Binance — миллисекунды; неофициальный фид — секунды."""
    v = float(value)
    if v > 1e14:      # микросекунды
        return int(v / 1_000_000)
    if v > 1e11:      # миллисекунды (формат data.binance.vision)
        return int(v / 1000)
    return int(v)


def _read_kline_csv(path: Path) -> pd.DataFrame:
    """Официальный файл Binance: с заголовком (файлы с 2025-01) или без него.

    ZIP читаем в память (месячный klines-архив — единицы МБ) и разбираем тот же
    парсер: ``pd.read_csv`` по `.zip` берёт «первый попавшийся» файл, а нам нужна
    конкретная CSV внутри — иначе сборка архивов зависит от порядка в архиве.
    """
    import io
    import zipfile

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if name is None:
                raise ValueError(f"{path}: в архиве нет CSV")
            buf = io.BytesIO(zf.read(name))
    else:
        buf = path.open("rb")
    try:
        first = buf.readline().decode("utf-8", "replace").strip()
        buf.seek(0)
        has_header = bool(first) and not first[:1].isdigit() and first[0] not in "-."
        df = pd.read_csv(buf, header=0 if has_header else None)
    finally:
        buf.close()
    if has_header:
        df.columns = [str(c).strip().lower() for c in df.columns]
    else:
        df.columns = BINANCE_KLINE_COLS[: len(df.columns)]
    return df


def load_binance_vision(
    root: Path, symbol: str, tf: str, years: tuple[int, ...] | None = None
) -> pd.DataFrame:
    """Собирает ОФИЦИАЛЬНЫЕ месячные/дневные архивы data.binance.vision.

    Ищет ``{root}/**/{SYMBOL}{quote}-{tf}-YYYY-MM(.csv|.zip)`` в любом вложенном
    виде (klines/spot/BTCUSDT/monthly/…, плоский каталог, выгрузка на диск).
    """
    sym = symbol.upper() + ("USDT" if symbol.upper() in ("BTC", "ETH", "SOL") else "")
    prefix = f"{sym}-{tf}-"
    files = sorted(
        p
        for p in list(root.rglob(prefix + "*.csv")) + list(root.rglob(prefix + "*.zip"))
        if ".DS_Store" not in p.name
    )
    if years:
        files = [p for p in files if any(f"-{y}" in p.name for y in years)]
    if not files:
        raise FileNotFoundError(
            f"{root}: не найдено ни одного {prefix}YYYY-MM.(csv|zip) — "
            f"проверь, что архив data.binance.vision распакован (klines/spot/{sym}/{tf}/)"
        )
    frames = [_read_kline_csv(p) for p in files]
    df = pd.concat(frames, ignore_index=True)
    if "open_time" not in df.columns:
        raise ValueError(f"{files[0]}: нет колонки open_time (ожидались {BINANCE_KLINE_COLS[:6]})")
    df = df.rename(columns={"open_time": "ts_raw"})
    need = {"ts_raw", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"{files[0]}: нет колонок {sorted(missing)}")
    df = df.assign(open_time=df["ts_raw"].map(_to_seconds))
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open_time", "open", "high", "low", "close"])
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    df.attrs["files"] = [p.name for p in files]
    return df


def scan_htf_gated_retest(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z6 = Z1 (ретест-вход разворота), но только когда дневная лента СОГЛАСУЕТ.

    Это честная версия провалившейся Z1: метод автора — конъюнкция
    «HTF-контекст → формация → ретест → реакция», прошлый суд проверял её без
    первого элемента.
    """
    base = scan_retest(bars)
    if not base:
        return []
    bias = daily_bias(bars)
    out: list[dict[str, Any]] = []
    for e in base:
        b = bias[e["entry_bar"]] if e["entry_bar"] < len(bias) else None
        ok = (b == "bull" and e["dir"] == "long") or (b == "bear" and e["dir"] == "short")
        if not Z6_STRICT_CONTEXT and b is None:
            ok = True
        if ok:
            out.append({**e, "meta": {**e["meta"], "htf_bias": b}})
    return out


def _z7_reacted(k: int, side: str, level: float, opens: list[float], closes: list[float]) -> bool:
    """Закрытие «в сторону входа»: зелёная свеча выше границы для лонга, зеркально для шорта."""
    if side == "long":
        return closes[k] > opens[k] and (not Z7_REQUIRE_INSIDE or closes[k] > level)
    return closes[k] < opens[k] and (not Z7_REQUIRE_INSIDE or closes[k] < level)


def _z7_failed(k: int, side: str, level: float, closes: list[float]) -> bool:
    """Зона пробита по close (для лонга — закрытие под поддержкой) → идея отменена."""
    if side == "long":
        return closes[k] < level * (1.0 - CH_TOUCH_TOL)
    return closes[k] > level * (1.0 + CH_TOUCH_TOL)


def scan_zone_reaction(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z7: вход у границы зоны ТОЛЬКО после реакции — первого закрытия в сторону входа.

    Формулировка автора: у зоны надо не «ловить нож», а дождаться реакции.
    Реализация (правило зафиксировано до прогона):

      * зона — границы последних подтверждённых свингов (``channel_structure``,
        то же зеркало ``detect_rectangle``, что и у Z5);
      * касание = подтверждённый свинг, чей экстремум лежит в допуске границы
        (``CH_TOUCH_TOL``); нужно ``>= Z7_MIN_TOUCHES`` касаний этой границы —
        первое касание свежего уровня не торгуем;
      * сигнал — ПЕРВОЕ закрытие в сторону входа после подтверждения касания
        (лонг: зелёная свеча, закрывшаяся выше поддержки; шорт зеркально), не
        позже ``Z7_REACTION_BARS`` баров; если раньше была реакция — входим по
        первой, а не по любой;
      * если до реакции цена закрылась за пределами зоны (пробой) — сигнала нет;
      * стоп — за экстремумом касания с ``STOP_BUFFER``, цель — противоположная
        граница, но не уже ``TARGET_MIN_RR``×R (как в Z5).

    Взгляд в будущее отсутствует: состояние на баре t считается по свингам,
    подтверждённым к t-1 (``i + SWING_W <= t - 1``).
    """
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    opens = [b["open"] for b in bars]
    closes = [b["close"] for b in bars]
    hi, lo = swings(highs, lows, SWING_W)
    hi_set = set(hi)
    lo_set = set(lo)
    entries: list[dict[str, Any]] = []
    for t in range(2 * SWING_W + 3, len(bars)):
        ch = channel_structure(highs, lows, hi, lo, t, SWING_W)
        if ch is None:
            continue
        res, sup = ch["res"], ch["sup"]
        for side, level, ext, idx_set in (
            ("long", sup, lows, lo_set),
            ("short", res, highs, hi_set),
        ):
            touches = [
                i for i in sorted(idx_set)
                if i + SWING_W <= t - 1 and abs(ext[i] - level) <= CH_TOUCH_TOL * max(level, 1.0)
            ]
            if len(touches) < Z7_MIN_TOUCHES:
                continue
            i = touches[-1]
            confirmed = i + SWING_W
            if t - confirmed > Z7_REACTION_BARS:
                continue  # реакция запоздала — это уже не «первое закрытие»
            if any(_z7_failed(k, side, level, closes) for k in range(confirmed + 1, t)):
                continue  # зона пробита по close → идея отменена
            if not _z7_reacted(t, side, level, opens, closes):
                continue
            if any(_z7_reacted(k, side, level, opens, closes) for k in range(confirmed + 1, t)):
                continue  # входим строго по ПЕРВОЙ реакции
            entry = closes[t]
            stop = ext[i] * (1.0 - STOP_BUFFER) if side == "long" else ext[i] * (1.0 + STOP_BUFFER)
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            target = res if side == "long" else sup
            min_target = entry + risk * TARGET_MIN_RR if side == "long" else entry - risk * TARGET_MIN_RR
            target = max(target, min_target) if side == "long" else min(target, min_target)
            entries.append(
                {
                    "entry_bar": t,
                    "dir": side,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "meta": {"kind": "zone_reaction", "touches": len(touches), "boundary": side},
                }
            )
    return entries


def verdict(oos: dict[str, Any] | None) -> str:
    """Метка силы ПО OOS. strong запрещена (PR #77): много гипотез, один срез."""
    if not oos:
        return "none"
    n = int(oos.get("n") or 0)
    mr = oos.get("mean_r")
    ci = oos.get("bootstrap_95_ci_mean_r") or [None, None]
    if mr is None:
        return "none"
    if n >= VERDICT_MODERATE_MIN_N and mr > 0 and ci[0] is not None and ci[0] > VERDICT_MODERATE_MIN_CI_LOW:
        return "moderate"
    if n >= VERDICT_WEAK_MIN_N and mr > 0:
        return "weak"
    return "none"


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        raise ValueError(f"{path}: missing {sorted(required - set(df.columns))}")
    dt = pd.to_datetime(df["timestamp"], utc=True)
    df = df.assign(open_time=(dt.astype("int64") // 1_000_000)).sort_values("open_time")
    df = df.drop_duplicates("open_time").reset_index(drop=True)
    return df


def _swings(highs: list[float], lows: list[float], w: int) -> tuple[list[int], list[int]]:
    hi: list[int] = []
    lo: list[int] = []
    for i in range(w, len(highs) - w):
        if highs[i] == max(highs[i - w : i + w + 1]):
            hi.append(i)
        if lows[i] == min(lows[i - w : i + w + 1]):
            lo.append(i)
    return hi, lo


def _collapse(idx: list[int], gap: int = PLATEAU_GAP) -> list[int]:
    if not idx:
        return []
    out: list[int] = []
    run: list[int] = [idx[0]]
    for i in idx[1:]:
        if i - run[-1] <= gap:
            run.append(i)
        else:
            out.append(run[len(run) // 2])
            run = [i]
    out.append(run[len(run) // 2])
    return out


def swings(highs: list[float], lows: list[float], w: int) -> tuple[list[int], list[int]]:
    hi, lo = _swings(highs, lows, w)
    return _collapse(hi), _collapse(lo)


def rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    """RSI (Wilder) по закрытиям; до разогрева — 0.0."""
    n = len(closes)
    rsi = [0.0] * n
    if n <= period:
        return rsi
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_g = sum(gains[1 : period + 1]) / period
    avg_l = sum(losses[1 : period + 1]) / period
    rsi[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rsi[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return rsi


# ---------------------------------------------------------------------------
# Разворотные фигуры (та же геометрия, что в pattern_strategies.py)
# ---------------------------------------------------------------------------
def reversal_structures(
    highs: list[float],
    lows: list[float],
    hi: list[int],
    lo: list[int],
    t: int,
    w: int,
    rsi: list[float],
) -> list[dict[str, Any]]:
    """Фигуры, у которых последний свинг подтверждён к бару ``t``.

    Возвращает список структур: kind, dir, extreme, neck, height, last_swing,
    swing_idx (список индексов экстремумов), divergence (bool, RSI 14).
    Свинги берутся только из прошлого (i + w <= t) — look-ahead нет.
    """
    out: list[dict[str, Any]] = []
    price = float(highs[t])
    hi_c = [i for i in hi if i + w <= t]
    lo_c = [i for i in lo if i + w <= t]

    def _div_tops(idxs: list[int]) -> bool:
        if len(idxs) < 2:
            return False
        p1, p2 = idxs[-2], idxs[-1]
        return highs[p2] >= highs[p1] and rsi[p2] < rsi[p1]

    def _div_troughs(idxs: list[int]) -> bool:
        if len(idxs) < 2:
            return False
        t1, t2 = idxs[-2], idxs[-1]
        return lows[t2] <= lows[t1] and rsi[t2] > rsi[t1]

    # двойная вершина / двойное дно
    for k in range(len(hi_c) - 1):
        p1, p2 = hi_c[k], hi_c[k + 1]
        v1, v2 = highs[p1], highs[p2]
        mv = max(v1, v2)
        if mv <= 0 or abs(v1 - v2) / mv > LEVEL_TOL:
            continue
        between = [lows[j] for j in range(p1, p2 + 1)]
        if not between:
            continue
        neck = min(between)
        height = mv - neck
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "double_top",
                "dir": "short",
                "extreme": mv,
                "neck": neck,
                "height": height,
                "last_swing": p2,
                "swing_idx": [p1, p2],
                "divergence": _div_tops([p1, p2]),
            }
        )
    for k in range(len(lo_c) - 1):
        t1, t2 = lo_c[k], lo_c[k + 1]
        v1, v2 = lows[t1], lows[t2]
        mv = min(v1, v2)
        if mv <= 0 or abs(v1 - v2) / mv > LEVEL_TOL:
            continue
        between = [highs[j] for j in range(t1, t2 + 1)]
        if not between:
            continue
        neck = max(between)
        height = neck - mv
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "double_bottom",
                "dir": "long",
                "extreme": mv,
                "neck": neck,
                "height": height,
                "last_swing": t2,
                "swing_idx": [t1, t2],
                "divergence": _div_troughs([t1, t2]),
            }
        )
    # тройная вершина / тройное дно
    for k in range(len(hi_c) - 2):
        p1, p2, p3 = hi_c[k], hi_c[k + 1], hi_c[k + 2]
        v1, v2, v3 = highs[p1], highs[p2], highs[p3]
        mv = max(v1, v2, v3)
        mn = min(v1, v2, v3)
        if mv <= 0 or (mv - mn) / mv > LEVEL_TOL:
            continue
        between = [lows[j] for j in range(p1, p3 + 1)]
        if not between:
            continue
        neck = min(between)
        height = mv - neck
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "triple_top",
                "dir": "short",
                "extreme": mv,
                "neck": neck,
                "height": height,
                "last_swing": p3,
                "swing_idx": [p1, p2, p3],
                "divergence": _div_tops([p2, p3]),
            }
        )
    for k in range(len(lo_c) - 2):
        t1, t2, t3 = lo_c[k], lo_c[k + 1], lo_c[k + 2]
        v1, v2, v3 = lows[t1], lows[t2], lows[t3]
        mv = min(v1, v2, v3)
        mx = max(v1, v2, v3)
        if mv <= 0 or (mx - mv) / mv > LEVEL_TOL:
            continue
        between = [highs[j] for j in range(t1, t3 + 1)]
        if not between:
            continue
        neck = max(between)
        height = neck - mv
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "triple_bottom",
                "dir": "long",
                "extreme": mv,
                "neck": neck,
                "height": height,
                "last_swing": t3,
                "swing_idx": [t1, t2, t3],
                "divergence": _div_troughs([t2, t3]),
            }
        )
    for s in out:
        s["neck_gap"] = NECK_GAP
    return out


def hs_structures(
    highs: list[float],
    lows: list[float],
    hi: list[int],
    lo: list[int],
    t: int,
    w: int,
    rsi: list[float],
) -> list[dict[str, Any]]:
    """Голова и плечи / перевёрнутая ГП (зеркало detect_head_shoulders)."""
    out: list[dict[str, Any]] = []
    price = float(highs[t])
    hi_c = [i for i in hi if i + w <= t]
    lo_c = [i for i in lo if i + w <= t]

    # обычная ГП (шорт): три свинг-хая a<b<c, голова выше плеч
    for k in range(len(hi_c) - 2):
        a, b, c = hi_c[k], hi_c[k + 1], hi_c[k + 2]
        ls, head, rs = highs[a], highs[b], highs[c]
        if not (head > ls and head > rs):
            continue
        prominence = head - max(ls, rs)
        if prominence <= price * 0.004:
            continue
        between = [j for j in lo_c if a < j < c]
        if len(between) < 2:
            continue
        t1, t2 = between[0], between[-1]
        neck = (lows[t1] + lows[t2]) / 2.0
        height = head - neck
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "head_shoulders",
                "dir": "short",
                "extreme": head,
                "neck": neck,
                "height": height,
                "last_swing": c,
                "swing_idx": [a, b, c],
                "right_shoulder": rs,
                "divergence": highs[b] >= highs[a] and rsi[b] < rsi[a],
            }
        )
    # перевёрнутая ГП (лонг)
    for k in range(len(lo_c) - 2):
        a, b, c = lo_c[k], lo_c[k + 1], lo_c[k + 2]
        ls, head, rs = lows[a], lows[b], lows[c]
        if not (head < ls and head < rs):
            continue
        prominence = min(ls, rs) - head
        if prominence <= price * 0.004:
            continue
        between = [j for j in hi_c if a < j < c]
        if len(between) < 2:
            continue
        t1, t2 = between[0], between[-1]
        neck = (highs[t1] + highs[t2]) / 2.0
        height = neck - head
        if height < price * MIN_HEIGHT_PCT:
            continue
        out.append(
            {
                "kind": "inverted_head_shoulders",
                "dir": "long",
                "extreme": head,
                "neck": neck,
                "height": height,
                "last_swing": c,
                "swing_idx": [a, b, c],
                "right_shoulder": rs,
                "divergence": lows[b] <= lows[a] and rsi[b] > rsi[a],
            }
        )
    for s in out:
        s["neck_gap"] = HS_NECK_GAP
    return out


def channel_structure(
    highs: list[float],
    lows: list[float],
    hi: list[int],
    lo: list[int],
    t: int,
    w: int,
) -> dict[str, Any] | None:
    """Прямоугольник/канал по последним свингам (зеркало detect_rectangle).

    res/sup — средние уровни последних хаёв/лоёв; нужно >=2 касания каждой
    границы; высота >= 0.3% цены. Используется для Z3 (ложный пробой) и
    Z5 (вход от границы). Только прошлые свинги (i + w <= t).
    """
    price = float(highs[t])
    hi_c = [i for i in hi if i + w <= t][-6:]
    lo_c = [i for i in lo if i + w <= t][-6:]
    if len(hi_c) < 2 or len(lo_c) < 2:
        return None
    r_highs = [highs[i] for i in hi_c]
    r_lows = [lows[i] for i in lo_c]
    res = sum(r_highs) / len(r_highs)
    sup = sum(r_lows) / len(r_lows)
    if res <= sup:
        return None
    height = res - sup
    if height < price * MIN_HEIGHT_PCT:
        return None
    tol = max(price * 0.003, height * 0.1)
    if sum(1 for v in r_highs if abs(v - res) <= tol) < 2:
        return None
    if sum(1 for v in r_lows if abs(v - sup) <= tol) < 2:
        return None
    return {"res": res, "sup": sup, "height": height}


# ---------------------------------------------------------------------------
# Сканы входов (без look-ahead; каждая структура даёт один вход на вариант)
# ---------------------------------------------------------------------------
def _baseline_stop_target(s: dict[str, Any], entry: float) -> tuple[float, float]:
    """Стоп/тейк базлайна — как в стратегиях Double/Triple/HS."""
    d = s["dir"]
    if s["kind"] == "head_shoulders":
        rs = s["right_shoulder"]
        head = s["extreme"]
        sl = rs + 0.5 * max(head - rs, entry * 0.005)
    elif s["kind"] == "inverted_head_shoulders":
        rs = s["right_shoulder"]
        head = s["extreme"]
        sl = rs - 0.5 * max(rs - head, entry * 0.005)
    elif d == "short":
        sl = s["extreme"] * (1.0 + STOP_BUFFER)
    else:
        sl = s["extreme"] * (1.0 - STOP_BUFFER)
    risk = abs(entry - sl)
    if risk <= 0:
        return sl, entry
    if d == "short":
        tp = entry - max(s["height"], risk * TARGET_MIN_RR)
    else:
        tp = entry + max(s["height"], risk * TARGET_MIN_RR)
    return sl, tp


def _all_structures(
    highs: list[float],
    lows: list[float],
    hi2: list[int],
    lo2: list[int],
    hi3: list[int],
    lo3: list[int],
    t: int,
    rsi: list[float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in reversal_structures(highs, lows, hi2, lo2, t, SWING_W, rsi):
        s["swing_w"] = SWING_W
        out.append(s)
    for s in hs_structures(highs, lows, hi3, lo3, t, HS_SWING_W, rsi):
        s["swing_w"] = HS_SWING_W
        out.append(s)
    return out


def scan_breakout(bars: list[dict[str, Any]], *, divergence_only: bool) -> list[dict[str, Any]]:
    """Базлайн «вход пробоем шеи закрытием» (опция: только дивергентные — Z2)."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    rsi = rsi_series(closes)
    hi2, lo2 = swings(highs, lows, SWING_W)
    hi3, lo3 = swings(highs, lows, HS_SWING_W)
    entries: list[dict[str, Any]] = []
    armed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for t in range(2 * HS_SWING_W + 2, len(bars)):
        for s in _all_structures(highs, lows, hi2, lo2, hi3, lo3, t, rsi):
            if s["last_swing"] + s["swing_w"] != t:
                continue  # структура только что подтверждена
            key = (s["kind"], round(s["neck"], 8), round(s["extreme"], 8))
            if key in armed:
                continue
            armed[key] = s
        done: list[tuple[Any, ...]] = []
        for key, s in armed.items():
            if divergence_only and not s["divergence"]:
                done.append(key)
                continue
            gap = s["neck_gap"]
            if s["dir"] == "short":
                broke = closes[t] < s["neck"] * (1.0 - gap)
            else:
                broke = closes[t] > s["neck"] * (1.0 + gap)
            if not broke:
                if t - s["last_swing"] > BREAKOUT_MAX_BARS:
                    done.append(key)
                continue
            entry = closes[t]
            sl, tp = _baseline_stop_target(s, entry)
            entries.append(
                {
                    "entry_bar": t,
                    "dir": s["dir"],
                    "entry": entry,
                    "stop": sl,
                    "target": tp,
                    "meta": {"kind": s["kind"], "divergence": s["divergence"]},
                }
            )
            done.append(key)
        for key in done:
            armed.pop(key, None)
    return entries


def scan_retest(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z1: вход на ПЕРВОМ ретесте шеи после пробоя, при реакции (закрытие в сторону)."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    rsi = rsi_series(closes)
    hi2, lo2 = swings(highs, lows, SWING_W)
    hi3, lo3 = swings(highs, lows, HS_SWING_W)
    entries: list[dict[str, Any]] = []
    armed: dict[tuple[Any, ...], dict[str, Any]] = {}
    retesting: dict[tuple[Any, ...], dict[str, Any]] = {}
    for t in range(2 * HS_SWING_W + 2, len(bars)):
        for s in _all_structures(highs, lows, hi2, lo2, hi3, lo3, t, rsi):
            if s["last_swing"] + s["swing_w"] != t:
                continue
            key = (s["kind"], round(s["neck"], 8), round(s["extreme"], 8))
            if key not in armed:
                armed[key] = s
        for key, s in list(armed.items()):
            if key in retesting:
                continue
            gap = s["neck_gap"]
            if s["dir"] == "short":
                broke = closes[t] < s["neck"] * (1.0 - gap)
            else:
                broke = closes[t] > s["neck"] * (1.0 + gap)
            if broke:
                retesting[key] = {"s": s, "state": "touch", "extreme": 0.0, "armed_at": t}
            elif t - s["last_swing"] > BREAKOUT_MAX_BARS:
                armed.pop(key, None)
        for key, st in list(retesting.items()):
            if t <= st["armed_at"]:
                continue  # ретест считается со СЛЕДУЮЩЕГО бара после пробоя
            s = st["s"]
            if s["dir"] == "short":
                touch = highs[t] >= s["neck"] * (1.0 - RETEST_TOL)
                reacted = closes[t] < s["neck"]
                failed = closes[t] > s["neck"] * (1.0 + RETEST_TOL)
            else:
                touch = lows[t] <= s["neck"] * (1.0 + RETEST_TOL)
                reacted = closes[t] > s["neck"]
                failed = closes[t] < s["neck"] * (1.0 - RETEST_TOL)
            if st["state"] == "touch":
                if not touch:
                    if t - s["last_swing"] > BREAKOUT_MAX_BARS + MAX_HOLD_BARS:
                        retesting.pop(key, None)
                    continue
                if s["dir"] == "short":
                    st["extreme"] = highs[t]
                else:
                    st["extreme"] = lows[t]
                if reacted:
                    entry = closes[t]
                    sl = (
                        st["extreme"] * (1.0 + STOP_BUFFER)
                        if s["dir"] == "short"
                        else st["extreme"] * (1.0 - STOP_BUFFER)
                    )
                    tp = s["neck"] - s["height"] if s["dir"] == "short" else s["neck"] + s["height"]
                    entries.append(
                        {
                            "entry_bar": t,
                            "dir": s["dir"],
                            "entry": entry,
                            "stop": sl,
                            "target": tp,
                            "meta": {"kind": s["kind"], "retest": True},
                        }
                    )
                    retesting.pop(key, None)
                else:
                    st["state"] = "reaction"
            else:  # reaction: ждём закрытия в сторону
                if s["dir"] == "short":
                    st["extreme"] = max(st["extreme"], highs[t])
                else:
                    st["extreme"] = min(st["extreme"], lows[t])
                if reacted:
                    entry = closes[t]
                    sl = (
                        st["extreme"] * (1.0 + STOP_BUFFER)
                        if s["dir"] == "short"
                        else st["extreme"] * (1.0 - STOP_BUFFER)
                    )
                    tp = s["neck"] - s["height"] if s["dir"] == "short" else s["neck"] + s["height"]
                    entries.append(
                        {
                            "entry_bar": t,
                            "dir": s["dir"],
                            "entry": entry,
                            "stop": sl,
                            "target": tp,
                            "meta": {"kind": s["kind"], "retest": True},
                        }
                    )
                    retesting.pop(key, None)
                elif failed:
                    retesting.pop(key, None)
    return entries


def scan_false_breakout(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z3: контр-вход после ложного пробоя и ЗАКРЕПЛЕНИЯ обратно внутрь (закрытие)."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    hi, lo = swings(highs, lows, SWING_W)
    entries: list[dict[str, Any]] = []
    for t in range(2 * SWING_W + 2, len(bars) - 1):
        ch = channel_structure(highs, lows, hi, lo, t, SWING_W)
        if ch is None:
            continue
        res, sup = ch["res"], ch["sup"]
        # ложный пробой вверх: закрытие выше res, затем закрытие обратно внутрь
        if closes[t] > res * (1.0 + CHANNEL_NECK_GAP):
            extreme = highs[t]
            for j in range(t + 1, min(len(bars), t + MAX_HOLD_BARS)):
                extreme = max(extreme, highs[j])
                if closes[j] < res:  # закрепление обратно внутрь (не фитиль)
                    sl = extreme * (1.0 + STOP_BUFFER)
                    entries.append(
                        {
                            "entry_bar": j,
                            "dir": "short",
                            "entry": closes[j],
                            "stop": sl,
                            "target": sup,
                            "meta": {"kind": "false_breakout_up"},
                        }
                    )
                    break
        elif closes[t] < sup * (1.0 - CHANNEL_NECK_GAP):
            extreme = lows[t]
            for j in range(t + 1, min(len(bars), t + MAX_HOLD_BARS)):
                extreme = min(extreme, lows[j])
                if closes[j] > sup:
                    sl = extreme * (1.0 - STOP_BUFFER)
                    entries.append(
                        {
                            "entry_bar": j,
                            "dir": "long",
                            "entry": closes[j],
                            "stop": sl,
                            "target": res,
                            "meta": {"kind": "false_breakout_down"},
                        }
                    )
                    break
    return entries


def scan_overshoot(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z4: третий экстремум тройной фигуры с перехаем/перелоем уровня."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    hi, lo = swings(highs, lows, SWING_W)
    entries: list[dict[str, Any]] = []
    for t in range(2 * SWING_W + 2, len(bars)):
        price = highs[t]
        hi_c = [i for i in hi if i + SWING_W <= t]
        lo_c = [i for i in lo if i + SWING_W <= t]
        # тройная вершина с перехаем третьего экстремума
        for k in range(len(hi_c) - 2):
            p1, p2, p3 = hi_c[k], hi_c[k + 1], hi_c[k + 2]
            v1, v2, v3 = highs[p1], highs[p2], highs[p3]
            level = max(v1, v2)
            if level <= 0 or abs(v1 - v2) / level > LEVEL_TOL:
                continue
            if v3 > level * (1.0 + OVERSHOOT_GAP) and closes[p3] < level:
                if p3 + SWING_W != t:
                    continue  # детект только на баре подтверждения (без look-ahead)
                between = [lows[j] for j in range(p1, p3 + 1)]
                if not between:
                    continue
                neck = min(between)
                height = level - neck
                if height < price * MIN_HEIGHT_PCT:
                    continue
                sl = v3 * (1.0 + STOP_BUFFER)
                entries.append(
                    {
                        "entry_bar": t,
                        "dir": "short",
                        "entry": closes[t],
                        "stop": sl,
                        "target": neck - height,
                        "meta": {"kind": "triple_top_overshoot"},
                    }
                )
        # тройное дно с перелоем
        for k in range(len(lo_c) - 2):
            t1, t2, t3 = lo_c[k], lo_c[k + 1], lo_c[k + 2]
            v1, v2, v3 = lows[t1], lows[t2], lows[t3]
            level = min(v1, v2)
            if level <= 0 or abs(v1 - v2) / level > LEVEL_TOL:
                continue
            if v3 < level * (1.0 - OVERSHOOT_GAP) and closes[t3] > level:
                if t3 + SWING_W != t:
                    continue
                between = [highs[j] for j in range(t1, t3 + 1)]
                if not between:
                    continue
                neck = max(between)
                height = neck - level
                if height < price * MIN_HEIGHT_PCT:
                    continue
                sl = v3 * (1.0 - STOP_BUFFER)
                entries.append(
                    {
                        "entry_bar": t,
                        "dir": "long",
                        "entry": closes[t],
                        "stop": sl,
                        "target": neck + height,
                        "meta": {"kind": "triple_bottom_overshoot"},
                    }
                )
    return entries


def scan_channel_touch(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Z5 (опц.): вход от границы канала на 3-м/4-м касании («не в середине»)."""
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    hi, lo = swings(highs, lows, SWING_W)
    entries: list[dict[str, Any]] = []
    for t in range(2 * SWING_W + 2, len(bars)):
        ch = channel_structure(highs, lows, hi, lo, t, SWING_W)
        if ch is None:
            continue
        res, sup = ch["res"], ch["sup"]
        hi_c = [i for i in hi if i + SWING_W <= t]
        lo_c = [i for i in lo if i + SWING_W <= t]
        sup_touch = [i for i in lo_c if abs(lows[i] - sup) <= CH_TOUCH_TOL * max(sup, 1.0)]
        res_touch = [i for i in hi_c if abs(highs[i] - res) <= CH_TOUCH_TOL * max(res, 1.0)]
        if len(sup_touch) in (3, 4):
            i = sup_touch[-1]
            if i == t - SWING_W:  # касание только что подтвердилось — вход от границы
                sl = lows[i] * (1.0 - STOP_BUFFER)
                entries.append(
                    {
                        "entry_bar": t,
                        "dir": "long",
                        "entry": closes[t],
                        "stop": sl,
                        "target": res,
                        "meta": {"kind": "channel_support_touch", "touches": len(sup_touch)},
                    }
                )
        if len(res_touch) in (3, 4):
            i = res_touch[-1]
            if i == t - SWING_W:
                sl = highs[i] * (1.0 + STOP_BUFFER)
                entries.append(
                    {
                        "entry_bar": t,
                        "dir": "short",
                        "entry": closes[t],
                        "stop": sl,
                        "target": sup,
                        "meta": {"kind": "channel_resistance_touch", "touches": len(res_touch)},
                    }
                )
    return entries


# ---------------------------------------------------------------------------
# Симуляция и метрики
# ---------------------------------------------------------------------------
def net_r(direction: str, entry: float, exitp: float, risk: float, cps: float) -> float:
    if direction == "long":
        fill_e = entry * (1.0 + cps)
        fill_x = exitp * (1.0 - cps)
        return (fill_x - fill_e) / risk
    fill_e = entry * (1.0 - cps)
    fill_x = exitp * (1.0 + cps)
    return (fill_e - fill_x) / risk


def simulate(bars: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Одна позиция на вариант одновременно; стоп проверяется раньше тейка
    (консервативно). Возвращает закрытые сделки с r/mfe/mae."""
    trades: list[dict[str, Any]] = []
    open_until = -1
    for e in sorted(entries, key=lambda x: x["entry_bar"]):
        i = e["entry_bar"]
        if i <= open_until:
            continue
        d, entry, stop, tgt = e["dir"], e["entry"], e["stop"], e["target"]
        risk = abs(entry - stop)
        if risk <= 0 or risk < entry * MIN_RISK_PCT:
            continue  # стоп уже издержек → сделка неиграбельна (фикс. правило)
        mfe = 0.0
        mae = 0.0
        exit_bar = i
        exit_price = entry
        reason = "time"
        last = min(len(bars) - 1, i + MAX_HOLD_BARS)
        for j in range(i + 1, last + 1):
            h, l = bars[j]["high"], bars[j]["low"]
            if d == "long":
                mfe = max(mfe, (h - entry) / risk)
                mae = min(mae, (l - entry) / risk)
                if l <= stop:
                    exit_bar, exit_price, reason = j, stop, "stop"
                    break
                if h >= tgt:
                    exit_bar, exit_price, reason = j, tgt, "target"
                    break
            else:
                mfe = max(mfe, (entry - l) / risk)
                mae = min(mae, (entry - h) / risk)
                if h >= stop:
                    exit_bar, exit_price, reason = j, stop, "stop"
                    break
                if l <= tgt:
                    exit_bar, exit_price, reason = j, tgt, "target"
                    break
        else:
            exit_bar = last
            exit_price = bars[last]["close"]
            reason = "time"
        trades.append(
            {
                "entry_bar": i,
                "exit_bar": exit_bar,
                "dir": d,
                "reason": reason,
                "r": net_r(d, entry, exit_price, risk, COST_PER_SIDE),
                "mfe_r": mfe,
                "mae_r": mae,
                "bars_held": exit_bar - i,
                "meta": e["meta"],
            }
        )
        open_until = exit_bar
    return trades


def bootstrap_ci(values: list[float], reps: int = RESAMPLES) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(SEED)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(reps))
    return [samples[int(0.025 * reps)], samples[int(0.975 * reps) - 1]]


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    rs = [t["r"] for t in trades]
    gp = sum(max(0.0, r) for r in rs)
    gl = -sum(min(0.0, r) for r in rs)
    if gl > 0:
        pf: float | str | None = round(gp / gl, 4)
    elif gp == 0:
        pf = None
    else:
        pf = "inf"  # только прибыльные сделки
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "n": n,
        "sum_r": round(sum(rs), 4) if rs else None,
        "pf": pf,
        "mean_r": round(mean(rs), 4) if rs else None,
        "median_r": round(median(rs), 4) if rs else None,
        "max_dd_r": round(mdd, 4),
        "mfe1r_share": round(sum(1 for t in trades if t["mfe_r"] >= 1.0) / n, 4) if n else None,
        "bootstrap_95_ci_mean_r": [round(v, 4) for v in ci] if (ci := bootstrap_ci(rs)) else None,
    }


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------
VARIANTS: dict[str, Any] = {
    "Z1_retest": {"scan": scan_retest, "label": "Z1 ретест-вход"},
    "Z2_divergence": {
        "scan": lambda bars: scan_breakout(bars, divergence_only=True),
        "label": "Z2 пробой + RSI-дивергенция",
    },
    "Z3_false_breakout": {
        "scan": scan_false_breakout,
        "label": "Z3 контр-вход после ложного пробоя",
    },
    "Z4_overshoot": {"scan": scan_overshoot, "label": "Z4 overshoot 3-го экстремума"},
    "Z5_channel_touch": {"scan": scan_channel_touch, "label": "Z5 вход от границы (3-4 касание)"},
    "Z6_htf_gated_retest": {
        "scan": scan_htf_gated_retest,
        "label": "Z6 ретест-вход + согласие дневной ленты EMA 20/50/100/200",
    },
    "Z7_zone_reaction": {
        "scan": scan_zone_reaction,
        "label": "Z7 вход у границы только после реакции (первое закрытие в сторону)",
    },
}

BASELINES: dict[str, Any] = {
    "Z1_retest": lambda bars: scan_breakout(bars, divergence_only=False),
    "Z2_divergence": lambda bars: scan_breakout(bars, divergence_only=False),
    "Z3_false_breakout": None,  # базлайн канала/прямоугольника — отдельно
    "Z4_overshoot": None,  # базлайн тройной фигуры — scan_breakout (triple)
    "Z5_channel_touch": None,
    # Z6 — это Z1 с добавленным HTF-контекстом: базлайном служит сама Z1.
    "Z6_htf_gated_retest": lambda bars: scan_retest(bars),
    # Z7 — Z5 с требованием реакции: базлайном служит вход от границы без реакции.
    "Z7_zone_reaction": lambda bars: scan_channel_touch(bars),
}


def run_symbol_tf(
    symbol: str, tf: str, df: pd.DataFrame
) -> tuple[dict[str, Any], dict[tuple[str, str], list[dict[str, Any]]]]:
    bars: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        bars.append(
            {
                "ts": int(row["open_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    n = len(bars)
    cut = int(n * SPLIT)
    res: dict[str, Any] = {"bars": n, "split_bar": cut, "in_sample": {}, "out_of_sample": {}}
    pool: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def record(window: str, name: str, entries: list[dict[str, Any]]) -> None:
        lo, hi = (0, cut) if window == "in_sample" else (cut, n)
        w_entries = [e for e in entries if lo <= e["entry_bar"] < hi]
        trades = simulate(bars, w_entries)
        res[window][name] = metrics(trades)
        pool.setdefault((window, name), []).extend(trades)

    for name, spec in VARIANTS.items():
        entries = spec["scan"](bars)
        record("in_sample", name, entries)
        record("out_of_sample", name, entries)

    # Метки силы — ТОЛЬКО по OOS (strong запрещена, см. verdict()).
    res["verdict_oos"] = {
        name: verdict(res["out_of_sample"].get(name)) for name in res["out_of_sample"]
    }
    # Сколько баров вообще имело HTF-контекст (иначе «Z6 не сработала» можно
    # спутать с «Z6 опровергнута»).
    try:
        _bias = daily_bias(bars)
        res["htf_context_coverage"] = round(
            sum(1 for b in _bias if b in ("bull", "bear")) / max(1, len(_bias)), 4
        )
    except Exception as exc:  # pragma: no cover
        res["htf_context_coverage"] = None
        res["htf_context_error"] = str(exc)

    # базлайны: пробой шеи для Z1/Z2 (та же фигура) и канала для Z3/Z5
    base_breakout = scan_breakout(bars, divergence_only=False)
    record("in_sample", "baseline_breakout", base_breakout)
    record("out_of_sample", "baseline_breakout", base_breakout)

    # базлайн канала/прямоугольника: вход пробоем границы закрытием
    def channel_breakout_scan(b: list[dict[str, Any]]) -> list[dict[str, Any]]:
        highs = [x["high"] for x in b]
        lows = [x["low"] for x in b]
        closes = [x["close"] for x in b]
        hi, lo = swings(highs, lows, 2)
        out: list[dict[str, Any]] = []
        for t in range(6, len(b)):
            ch = channel_structure(highs, lows, hi, lo, t, 2)
            if ch is None:
                continue
            if closes[t] > ch["res"] * (1.0 + CHANNEL_NECK_GAP):
                risk = closes[t] - ch["sup"]
                out.append(
                    {
                        "entry_bar": t,
                        "dir": "long",
                        "entry": closes[t],
                        "stop": ch["sup"],
                        "target": closes[t] + max(ch["height"], risk * TARGET_MIN_RR),
                        "meta": {"kind": "channel_breakout_up"},
                    }
                )
            elif closes[t] < ch["sup"] * (1.0 - CHANNEL_NECK_GAP):
                risk = ch["res"] - closes[t]
                out.append(
                    {
                        "entry_bar": t,
                        "dir": "short",
                        "entry": closes[t],
                        "stop": ch["res"],
                        "target": closes[t] - max(ch["height"], risk * TARGET_MIN_RR),
                        "meta": {"kind": "channel_breakout_down"},
                    }
                )
        return out

    ch_base = channel_breakout_scan(bars)
    record("in_sample", "baseline_channel_breakout", ch_base)
    record("out_of_sample", "baseline_channel_breakout", ch_base)

    # базлайн тройной фигуры для Z4: пробой шеи только по triple
    triple_base = [e for e in base_breakout if e["meta"]["kind"].startswith("triple")]
    record("in_sample", "baseline_triple_breakout", triple_base)
    record("out_of_sample", "baseline_triple_breakout", triple_base)

    return res, pool



def _synthetic_bars(n: int, seed: int, tf: str) -> list[dict[str, Any]]:
    """Детерминированная синтетика: дрейфующий случайное-блуждающий ряд с режимами."""
    rng = random.Random(seed)
    step = {"1h": 3600, "4h": 14400, "1d": 86400}.get(tf, 3600)
    out: list[dict[str, Any]] = []
    price = 100.0
    t = 1_600_000_000
    drift = 0.0
    for i in range(n):
        if i % 400 == 0:
            drift = rng.choice((-0.0006, -0.0002, 0.0, 0.0002, 0.0006))
        r = drift + rng.gauss(0.0, 0.006)
        o = price
        price = max(1.0, price * (1.0 + r))
        c = price
        hi = max(o, c) * (1.0 + abs(rng.gauss(0.0, 0.002)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, 0.002)))
        out.append({
            "ts": t + i * step, "open": o, "high": hi, "low": lo, "close": c,
            "volume": 1000.0 * (1.0 + abs(r) * 50.0),
        })
    return out


def self_test() -> dict[str, Any]:
    """Механика без данных: парсеры Official CSV, лента HTF, детерминизм, метки.

    Нужен ровно на период «ветка с архивами ещё не прилетела»: он доказывает,
    что пайплин готов к перезапуску, и не проверяет гипотезы (для этого нужны
    официльные бары).
    """
    import tempfile

    res: dict[str, Any] = {"mode": "self-test", "checks": {}}

    # 1) парсер: официальный CSV без заголовка и с заголовком (с 2025-01)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        flat = root / "klines" / "spot" / "BTCUSDT" / "1h"
        flat.mkdir(parents=True)
        ms = 1_600_000_000 * 1000
        rows_no_header = "\n".join(
            f"{ms + i * 3600000},{100 + i},{101 + i},{99 + i},{100.5 + i},1000,"
            f"{ms + (i + 1) * 3600000 - 1},100000,{100 + i},500,50000,0"
            for i in range(5)
        )
        (flat / "BTCUSDT-1h-2020-09.csv").write_text(rows_no_header + "\n", encoding="utf-8")
        rows_header = (
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore\n"
            + rows_no_header + "\n"
        )
        (flat / "BTCUSDT-1h-2025-01.csv").write_text(rows_header, encoding="utf-8")
        df = load_binance_vision(root, "BTC", "1h")
        res["checks"]["loader_parses_both_forms"] = bool(len(df) == 5 and "open_time" in df.columns)
        res["checks"]["loader_open_time_seconds"] = bool(int(df.iloc[0]["open_time"]) == 1_600_000_000)
        try:
            load_binance_vision(root, "SOL", "4h")
            res["checks"]["loader_missing_raises"] = False
        except FileNotFoundError:
            res["checks"]["loader_missing_raises"] = True

    # 2) HTF-лента: None, пока нет 200 ЗАКРЫТЫХ дневных баров, затем определяется.
    # Отдельно и дёшево: daily_bias — O(n), а сканы фигур — O(n·структур).
    bars_long = _synthetic_bars(24 * 210, 7, "1h")  # 210 дней часовых баров
    bias = daily_bias(bars_long)
    n_first_days = 24 * RIBBON_MIN_DAILY_BARS  # баров до дня, когда лента становится доступной
    res["checks"]["ribbon_requires_history"] = all(
        b is None for b in bias[: n_first_days - 24]
    )
    res["checks"]["ribbon_becomes_available"] = any(
        b in ("bull", "bear", "neutral") for b in bias[n_first_days:]
    )
    # текущий (незавершённый) день не участвует в ленте — подглядывания нет
    res["checks"]["ribbon_never_looks_ahead"] = all(
        bias[i] == bias[i + 1] for i in range(n_first_days, len(bias) - 25, 24)
    )

    # 3) Z6/Z7 исполняются и не падают; Z6 ⊆ Z1 (фильтр может только убирать)
    bars = _synthetic_bars(2500, 11, "1h")
    z1 = scan_retest(bars)
    z6 = scan_htf_gated_retest(bars)
    z7 = scan_zone_reaction(bars)
    key = lambda e: (e["entry_bar"], e["dir"])  # noqa: E731
    res["checks"]["z6_subset_of_z1"] = all(key(e) in {key(x) for x in z1} for e in z6)
    res["checks"]["z7_produces_valid_geometry"] = all(
        (e["dir"] == "long" and e["stop"] < e["entry"] < e["target"])
        or (e["dir"] == "short" and e["target"] < e["entry"] < e["stop"])
        for e in z7
    )
    res["z6_z7_counts"] = {"z1": len(z1), "z6": len(z6), "z7": len(z7)}

    # 4) детерминизм: два прогона подряд = побайтово одинаковый результат
    import pandas as pd

    df = pd.DataFrame([
        {"open_time": b["ts"], "open": b["open"], "high": b["high"],
         "low": b["low"], "close": b["close"], "volume": b["volume"]}
        for b in bars
    ])
    a, _ = run_symbol_tf("BTC", "1h", df)
    b2, _ = run_symbol_tf("BTC", "1h", df)
    res["checks"]["determinism"] = json.dumps(a, sort_keys=True) == json.dumps(b2, sort_keys=True)

    # 5) метки силы: strong не выдаётся никогда
    res["checks"]["strong_never_emitted"] = all(
        verdict(m) in ("moderate", "weak", "none")
        for m in (
            {"n": 10_000, "mean_r": 1.0, "bootstrap_95_ci_mean_r": [0.5, 0.9]},
            {"n": 40, "mean_r": 0.1, "bootstrap_95_ci_mean_r": [-0.1, 0.3]},
            {"n": 5, "mean_r": 0.9, "bootstrap_95_ci_mean_r": [0.1, 1.0]},
            None,
        )
    )
    res["ready_for_data"] = all(v is True for v in res["checks"].values())
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Track C replay (PR #77 + PR #78)")
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="каталог с Binance_{BTC,ETH,SOL}USDT_{1h,4h}.csv (неофициальный фид, PR #77)",
    )
    ap.add_argument(
        "--source",
        choices=("unofficial", "binance-vision"),
        default="unofficial",
        help="binance-vision — официальные месячные архивы data.binance.vision (PR #78)",
    )
    ap.add_argument(
        "--archive-dir",
        type=Path,
        default=None,
        help="корень распакованных архивов data.binance.vision (для --source binance-vision)",
    )
    ap.add_argument("--years", default="", help="фильтр по годам, напр. 2021,2022,2023")
    ap.add_argument("--provenance", default="", help="источник данных (repo @ commit / URL)")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="прогнать детерминизм и парсеры на синтетике (когда данных ещё нет)",
    )
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return

    if args.source == "binance-vision":
        if args.archive_dir is None:
            raise SystemExit("--source binance-vision требует --archive-dir")
        loader = lambda sym, tf: load_binance_vision(  # noqa: E731
            args.archive_dir, sym, tf, years=tuple(int(y) for y in args.years.split(",") if y.strip())
        )
        provenance = args.provenance or "data.binance.vision (official spot klines, monthly)"
    else:
        if args.data_dir is None:
            raise SystemExit(
                "ждёт данных: нужен либо --archive-dir с официальными архивами "
                "data.binance.vision, либо --data-dir с фидом PR #77 "
                "(--self-test проверяет механику без данных)"
            )
        loader = lambda sym, tf: load(args.data_dir / f"Binance_{sym}USDT_{tf}.csv")  # noqa: E731
        provenance = args.provenance or "unofficial"

    output: dict[str, Any] = {
        "seed": SEED,
        "resamples": RESAMPLES,
        "split": SPLIT,
        "provenance": provenance,
        "source": args.source,
        "ribbon_source": RIBBON_SOURCE,
        "cost_round_trip_pct": COST_PER_SIDE * 2 * 100,
        "rules": {
            "level_tol": LEVEL_TOL,
            "min_height_pct": MIN_HEIGHT_PCT,
            "swing_w": SWING_W,
            "hs_swing_w": HS_SWING_W,
            "neck_gap": NECK_GAP,
            "hs_neck_gap": HS_NECK_GAP,
            "channel_neck_gap": CHANNEL_NECK_GAP,
            "stop_buffer": STOP_BUFFER,
            "target_min_rr": TARGET_MIN_RR,
            "min_risk_pct": MIN_RISK_PCT,
            "max_hold_bars": MAX_HOLD_BARS,
            "breakout_max_bars": BREAKOUT_MAX_BARS,
            "retest_tol": RETEST_TOL,
            "channel_touch_tol": CH_TOUCH_TOL,
            "overshoot_gap": OVERSHOOT_GAP,
            "rsi_period": RSI_PERIOD,
            "z6_strict_context": Z6_STRICT_CONTEXT,
            "ribbon_min_daily_bars": RIBBON_MIN_DAILY_BARS,
            "ribbon_emas": list(RIBBON_EMAS),
            "z7_min_touches": Z7_MIN_TOUCHES,
            "z7_require_inside": Z7_REQUIRE_INSIDE,
            "verdict_rules": {
                "strong": "запрещена (PR #77): много гипотез на одном OOS-срезе",
                "moderate": f"OOS n>={VERDICT_MODERATE_MIN_N} и mean_r>0 и нижняя CI>0",
                "weak": f"OOS n>={VERDICT_WEAK_MIN_N} и mean_r>0",
            },
        },
        "symbols": {},
    }
    pooled: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for tf in TIMEFRAMES:
        for sym in SYMBOLS:
            try:
                df = loader(sym, tf)
            except FileNotFoundError as exc:
                output["symbols"][f"{sym}USDT|{tf}"] = {"error": str(exc), "status": "ждёт данных"}
                continue
            if len(df) < 400:
                output["symbols"][f"{sym}USDT|{tf}"] = {
                    "error": f"слишком короткая история: {len(df)} баров (нужно >= 400)",
                    "bars": len(df),
                }
                continue
            res, pool = run_symbol_tf(sym, tf, df)
            output["symbols"][f"{sym}USDT|{tf}"] = res
            for k, v in pool.items():
                pooled.setdefault(k, []).extend(v)
    output["pooled"] = {
        f"{w}|{name}": metrics(trades) for (w, name), trades in sorted(pooled.items())
    }
    output["honest_label"] = (
        "Exploratory only: короткое окно, один фид (Binance-labelled, provenance "
        "неофициальный), много гипотез на одних данных, IID-bootstrap; сильные "
        "метки не присваиваются (multiple testing). Выводы — только по OOS."
    )
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
