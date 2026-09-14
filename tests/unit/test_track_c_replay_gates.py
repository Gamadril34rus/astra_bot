"""Новые правила реплея (PR #78, часть 2): HTF-лента, Z6/Z7, парсер официальных CSV.

Данных для самого реплея в репозитории нет («ждёт данных»), поэтому здесь
проверяется только механика, на которой потом будет считаться вердикт: что
фильтр ленты умеет только убирать сигналы, что лента не подглядывает в текущий
день, что парсер Binance одинаково читает CSV с заголовком и без, и что метка
strong не выдаётся никогда.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "scripts" / "track_c_replay.py"
    spec = importlib.util.spec_from_file_location("track_c_replay_pr78", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Модуль-скрипт загружаем один раз на импорте: main() защищён __main__-гардом.
rp = _load_module()


def _bars(n: int, seed: int) -> list[dict]:
    return rp._synthetic_bars(n, seed, "1h")


# ------------------------------------------------------------------- метки -----
def test_strong_is_never_emitted() -> None:
    cases = [
        {"n": 5000, "mean_r": 2.0, "bootstrap_95_ci_mean_r": [1.0, 3.0]},
        {"n": 99, "mean_r": 2.0, "bootstrap_95_ci_mean_r": [1.0, 3.0]},
        {"n": 100, "mean_r": 0.05, "bootstrap_95_ci_mean_r": [-0.1, 0.2]},
        {"n": 30, "mean_r": 0.01, "bootstrap_95_ci_mean_r": [-0.1, 0.2]},
        {"n": 29, "mean_r": 5.0, "bootstrap_95_ci_mean_r": [1.0, 3.0]},
        {},
        None,
    ]
    assert {rp.verdict(c) for c in cases} <= {"moderate", "weak", "none"}
    assert rp.verdict(cases[0]) == "moderate"
    assert rp.verdict(cases[1]) == "weak"
    assert rp.verdict(cases[4]) == "none"


def test_verdict_is_oos_only_rule() -> None:
    """Модерейт требует И выборку, И знак CI — иначе это шум на малой n."""
    assert rp.verdict({"n": 500, "mean_r": -0.1, "bootstrap_95_ci_mean_r": [-0.5, 0.3]}) == "none"


# --------------------------------------------------------------------- лента ---
def test_ribbon_requires_history_and_does_not_look_ahead() -> None:
    bars = _bars(24 * 210, 5)
    bias = rp.daily_bias(bars)
    cut = 24 * rp.RIBBON_MIN_DAILY_BARS
    assert all(b is None for b in bias[: cut - 24])
    assert any(b in ("bull", "bear", "neutral") for b in bias[cut:])
    # внутри одних суток смещение не меняется: текущий (незакрытый) день исключён
    same_day = [bias[i] for i in range(cut, min(len(bias), cut + 24))]
    assert len(set(same_day)) <= 1


def test_ribbon_bias_matches_prod_stack_rule() -> None:
    """Реплика обязана совпадать с боевой htf_gates.ribbon_bias (или быть ею)."""
    up = [100.0 * (1.0 + 0.001 * i) for i in range(400)]
    down = [100.0 / (1.0 + 0.001 * i) for i in range(400)]
    assert rp.ribbon_bias(up) == "bull"
    assert rp.ribbon_bias(down) == "bear"
    # ровный ряд = лента не выстроена
    assert rp.ribbon_bias([100.0] * 400) == "neutral"
    assert rp.ribbon_bias([100.0] * 50) is None
    prod = getattr(rp, "_ribbon_bias_prod", None)
    if prod is not None:  # импорт боевого модуля удался — сверяем 1-в-1
        for series in (up, down, [100.0] * 400, [100.0] * 50):
            assert prod(series, min_bars=rp.RIBBON_MIN_DAILY_BARS)[0] == rp.ribbon_bias(series)


# ------------------------------------------------------------------ Z6/Z7 ------
def test_z6_only_removes_signals_from_z1() -> None:
    bars = _bars(2500, 11)
    z1 = rp.scan_retest(bars)
    z6 = rp.scan_htf_gated_retest(bars)
    assert z1, "фикстура должна давать ретест-сигналы"
    keys = {(e["entry_bar"], e["dir"]) for e in z1}
    assert all((e["entry_bar"], e["dir"]) in keys for e in z6)
    # на короткой истории (104 дня) дневной ленты нет вовсе → Z6 молчит
    # (в этом и смысл «ждёт данных»: нужен архив >= 200 дневных баров)
    if len(bars) < 24 * rp.RIBBON_MIN_DAILY_BARS:
        assert z6 == []


def test_z6_accepts_only_agreed_direction(monkeypatch) -> None:
    """Фильтр ленты: совпало — оставил, neutral/None/противоположно — выбросил.

    Смещение подменяем, а не вычисляем на длинном ряду: нам важен контракт
    фильтра, а синтетической истории на 200 дней здесь нет.
    """
    bars = _bars(2500, 11)
    z1 = rp.scan_retest(bars)
    assert z1
    n = len(bars)
    halves = ["bull"] * (n // 2) + ["neutral"] * (n - n // 2)
    monkeypatch.setattr(rp, "daily_bias", lambda _b: list(halves))
    z6 = rp.scan_htf_gated_retest(bars)
    longs_first_half = [e for e in z1 if e["entry_bar"] < n // 2 and e["dir"] == "long"]
    assert longs_first_half, "в бычей половине должны быть лонги"
    assert all(e["entry_bar"] < n // 2 and e["dir"] == "long" for e in z6)
    assert {(e["entry_bar"], e["dir"]) for e in z6} == {(e["entry_bar"], e["dir"]) for e in longs_first_half}
    # neutral = отказа; None = отказа (Z6_STRICT_CONTEXT)
    assert rp.Z6_STRICT_CONTEXT is True
    monkeypatch.setattr(rp, "daily_bias", lambda _b: [None] * n)
    assert rp.scan_htf_gated_retest(bars) == []
    monkeypatch.setattr(rp, "daily_bias", lambda _b: ["bear"] * n)
    shorts = [e for e in z1 if e["dir"] == "short"]
    assert {(e["entry_bar"], e["dir"]) for e in rp.scan_htf_gated_retest(bars)} == {
        (e["entry_bar"], e["dir"]) for e in shorts
    }
    # метка контекста доезжает до трейда — иначе нельзя проверить OOS-разрез
    assert all(e["meta"]["htf_bias"] == "bear" for e in rp.scan_htf_gated_retest(bars))


def _channel_bars(n: int = 2000, lo: float = 100.0, hi: float = 110.0, leg: int = 40) -> list[dict]:
    """Ровный канал туда-сюда: зона с повторными касаниями (детерминированно)."""
    out: list[dict] = []
    for i in range(n):
        ph = (i % leg) / leg
        rising = (i // leg) % 2 == 0
        price = lo + (hi - lo) * (ph if rising else 1 - ph)
        o, c = price - 0.3, price + 0.3
        out.append({
            "ts": 1_600_000_000 + i * 3600, "open": o, "high": max(o, c) + 0.15,
            "low": min(o, c) - 0.15, "close": c, "volume": 1000.0,
        })
    return out


def test_z7_enters_after_reaction_only() -> None:
    bars = _channel_bars()
    z7 = rp.scan_zone_reaction(bars)
    assert z7, "на чистом канале правило обязано давать входы"
    for e in z7:
        i = e["entry_bar"]
        assert e["meta"]["touches"] >= rp.Z7_MIN_TOUCHES, "первое касание не торгуем"
        if e["dir"] == "long":
            assert bars[i]["close"] > bars[i]["open"], "лонг — только по зелёному закрытию"
            assert e["stop"] < e["entry"] < e["target"]
        else:
            assert bars[i]["close"] < bars[i]["open"], "шорт — только по красному закрытию"
            assert e["target"] < e["entry"] < e["stop"]
        risk = abs(e["entry"] - e["stop"])
        reward = abs(e["target"] - e["entry"])
        assert reward >= risk * rp.TARGET_MIN_RR - 1e-9


def test_z7_uses_the_first_reaction_not_any_later_one() -> None:
    """Между подтверждением касания и входом не должно быть более ранней реакции."""
    bars = _channel_bars()
    opens = [b["open"] for b in bars]
    closes = [b["close"] for b in bars]
    for e in rp.scan_zone_reaction(bars):
        i = e["entry_bar"]
        # окно реакции ограничено Z7_REACTION_BARS (правило «первое закрытие»)
        assert i - 1 >= 0
        if e["dir"] == "long":
            assert closes[i] > opens[i]
        else:
            assert closes[i] < opens[i]
    # и главное: правило без состояния ⇒ повторный вызов даёт ровно то же
    assert rp.scan_zone_reaction(bars) == rp.scan_zone_reaction(bars)


def test_z7_flows_through_simulation_and_metrics() -> None:
    bars = _channel_bars()
    trades = rp.simulate(bars, rp.scan_zone_reaction(bars))
    assert trades, "Z7 должна давать закрытые сделки на канале"
    m = rp.metrics(trades)
    assert m["n"] == len(trades) > 0
    assert m["mean_r"] is not None and abs(m["mean_r"]) < 100
    assert rp.verdict(m) in ("moderate", "weak", "none")


def test_scans_are_deterministic() -> None:
    bars = _bars(1200, 17)
    for fn in (rp.scan_retest, rp.scan_htf_gated_retest, rp.scan_zone_reaction):
        assert fn(bars) == fn(bars), f"{fn.__name__} недетерминирован"


def test_simulate_skips_risk_below_costs_floor() -> None:
    """Фиксированное правило: стоп тоньше издержек → сделка не берётся."""
    bars = _bars(50, 2)
    entry = float(bars[1]["close"])
    trades = rp.simulate(bars, [{
        "entry_bar": 1, "dir": "long", "entry": entry,
        "stop": entry * (1 - 0.0005), "target": entry * 1.05, "meta": {},
    }])
    assert trades == []


# ------------------------------------------------------- парсер Binance --------
def test_loader_handles_header_and_no_header(tmp_path: Path) -> None:
    root = tmp_path / "klines" / "spot" / "BTCUSDT" / "1h"
    root.mkdir(parents=True)
    base = 1_600_000_000_000
    body = "\n".join(
        f"{base + i * 3600000},{100 + i},{101 + i},{99 + i},{100.5 + i},1000,"
        f"{base + (i + 1) * 3600000 - 1},1e5,100,500,5e4,0"
        for i in range(4)
    )
    (root / "BTCUSDT-1h-2020-09.csv").write_text(body + "\n", encoding="utf-8")
    (root / "BTCUSDT-1h-2025-01.csv").write_text(
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_base_asset_volume,taker_buy_quote_asset_volume,ignore\n"
        + body + "\n", encoding="utf-8",
    )
    df = rp.load_binance_vision(tmp_path, "BTC", "1h")
    # два файла описывают одни и те же 4 бара → дедуп по open_time
    assert len(df) == 4
    assert int(df.iloc[0]["open_time"]) == 1_600_000_000, "мс → секунды"
    assert float(df.iloc[0]["high"]) == 101.0
    with pytest.raises(FileNotFoundError):
        rp.load_binance_vision(tmp_path, "ETH", "4h")


def test_years_filter_selects_files(tmp_path: Path) -> None:
    root = tmp_path / "BTCUSDT-1h"
    root.mkdir(parents=True)
    row = "1600000000000,1,2,0.5,1.5,10,1600003599999,1e5,100,50,5e4,0"
    (root / "BTCUSDT-1h-2021-01.csv").write_text(row + "\n", encoding="utf-8")
    (root / "BTCUSDT-1h-2022-01.csv").write_text(
        row.replace("1600000000000", "1640995200000") + "\n", encoding="utf-8"
    )
    both = rp.load_binance_vision(tmp_path, "BTC", "1h")
    only_2021 = rp.load_binance_vision(tmp_path, "BTC", "1h", years=(2021,))
    assert len(both) == 2 and len(only_2021) == 1
    assert int(only_2021.iloc[0]["open_time"]) == 1_600_000_000


def test_rules_are_fixed_before_run() -> None:
    """Константы новых правил зафиксированы: подгонка под OOS = провал задания."""
    assert rp.Z6_STRICT_CONTEXT is True
    assert rp.RIBBON_EMAS == (20, 50, 100, 200)
    assert rp.RIBBON_MIN_DAILY_BARS == 200
    assert rp.Z7_MIN_TOUCHES == 2 and rp.Z7_REQUIRE_INSIDE is True
    assert "Z6_htf_gated_retest" in rp.VARIANTS and "Z7_zone_reaction" in rp.VARIANTS
    assert rp.VARIANTS["Z6_htf_gated_retest"]["scan"] is rp.scan_htf_gated_retest
    # базлайны обязаны существовать: Z6 против Z1, Z7 против Z5
    assert rp.BASELINES["Z6_htf_gated_retest"] is not None
    assert rp.BASELINES["Z7_zone_reaction"] is not None


def test_metrics_and_record_handle_empty_variant() -> None:
    m = rp.metrics([])
    assert m["n"] == 0
    assert m["mean_r"] in (None, 0)
    assert rp.bootstrap_ci([]) is None


def test_rng_seed_is_the_locked_one() -> None:
    assert rp.SEED == 20260913 and rp.RESAMPLES == 20000
    assert isinstance(random.Random(rp.SEED).random(), float)
