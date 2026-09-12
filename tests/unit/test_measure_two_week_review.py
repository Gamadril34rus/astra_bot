"""Тесты среза двухнедельного обзора (``scripts/measure_two_week_review.py``).

Проверяем на ФИКСТУРНЫХ файлах во временном каталоге (никакой записи в
``models/``): секции отчёта, арифметика бакетов, правило kill-switch и его
дедуплицированный вариант, сверку ledger с брокером, разбивку shadow-банов,
окно сделок — и главное требование «только чтение»: после прогона байты
фикстур не менялись и новых файлов не появилось.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.measure_two_week_review import (
    StateSource,
    _parse_as_of,
    main,
    render_report,
)

AS_OF = "2026-09-12T06:13Z"
AS_OF_MS = _parse_as_of(AS_OF)
DAY_MS = 86_400_000


def _ms(days_ago: float) -> int:
    return int(AS_OF_MS - days_ago * DAY_MS)


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


@pytest.fixture()
def models_dir(tmp_path: Path) -> Path:
    d = tmp_path / "models"
    d.mkdir()

    # --- strategy_stats.json: |ANY| + режимные бакеты (двойной счёт) ---
    (d / "strategy_stats.json").write_text(
        json.dumps(
            {
                "updated": "2026-09-12T06:00:00+00:00",
                "buckets": {
                    # momentum: дедуп n=6 -> kill и так, и при дедупе
                    "momentum|ANY|1h": {
                        "sample_size": 6, "wins": 1, "losses": 5, "sum_r": -2.0,
                        "wins_sum_r": 0.5, "losses_sum_r": -2.5,
                    },
                    "momentum|T:RANGE/V:LOW/L:NORMAL|1h": {
                        "sample_size": 6, "wins": 1, "losses": 5, "sum_r": -2.0,
                        "wins_sum_r": 0.5, "losses_sum_r": -2.5,
                    },
                    # advance: n_any=3 (правило молчит), n_all=6 -> «вырезал бы авансом»
                    "advance|ANY|1h": {
                        "sample_size": 3, "wins": 0, "losses": 3, "sum_r": -2.0,
                        "wins_sum_r": 0.0, "losses_sum_r": -2.0,
                    },
                    "advance|LOW_VOLATILITY|1h": {
                        "sample_size": 3, "wins": 0, "losses": 3, "sum_r": -2.0,
                        "wins_sum_r": 0.0, "losses_sum_r": -2.0,
                    },
                    # ghost: мало данных (n=2) — не трогает
                    "ghost|ANY|1h": {
                        "sample_size": 2, "wins": 0, "losses": 2, "sum_r": -1.0,
                        "wins_sum_r": 0.0, "losses_sum_r": -1.0,
                    },
                    # maicross (из списка «новых»): есть бакет, PF>1 — не трогает
                    "maicross|ANY|1h": {
                        "sample_size": 7, "wins": 6, "losses": 1, "sum_r": 2.0,
                        "wins_sum_r": 3.0, "losses_sum_r": -1.0,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # --- paper_trades.jsonl: 4 сделки в окне; Σfees == 0.40 ---
    _write(
        d / "paper_trades.jsonl",
        [
            {
                "id": "t1", "symbol": "BTC-USDT", "direction": "long",
                "exit_reason": "stop_loss", "strategy": "momentum",
                "pnl": "-1.5", "fees": "0.10", "funding": "0",
                "r_multiple": -1.05, "opened_at": _ms(5.5), "closed_at": _ms(5.2),
            },
            {
                "id": "t2", "symbol": "ETH-USDT", "direction": "long",
                "exit_reason": "stop_loss", "strategy": "momentum",
                "pnl": "-0.4", "fees": "0.20", "funding": "0",
                "r_multiple": -0.2, "opened_at": _ms(4.5), "closed_at": _ms(4.0),
            },
            {
                "id": "t3", "symbol": "SOL-USDT", "direction": "short",
                "exit_reason": "tp1", "strategy": "maicross",
                "pnl": "0.8", "fees": "0.05", "funding": "0",
                "r_multiple": 0.5, "opened_at": _ms(3.5), "closed_at": _ms(3.0),
            },
            {
                "id": "t4", "symbol": "XRP-USDT", "direction": "long",
                "exit_reason": "VOL_EXPANSION", "strategy": "ghost",
                "pnl": "-0.5", "fees": "0.05", "funding": "0",
                "r_multiple": -0.3, "opened_at": _ms(2.5), "closed_at": _ms(2.0),
            },
        ],
    )

    # --- paper_ledger.jsonl: fee только в fee-строках, Σfee == 0.40 ---
    _write(
        d / "paper_ledger.jsonl",
        [
            {"kind": "order", "event_id": "order:p1:open", "symbol": "BTC-USDT",
             "qty": "1", "price": "100", "fee": "0"},
            {"kind": "fill", "event_id": "fill:p1:open", "symbol": "BTC-USDT",
             "position_delta": "1", "fee": "0"},
            {"kind": "fee", "event_id": "fee:p1:open", "symbol": "BTC-USDT", "fee": "0.10"},
            {"kind": "fill", "event_id": "fill:p1:close", "symbol": "BTC-USDT",
             "position_delta": "-1", "cash_delta": "-1.5", "pnl": "-1.5", "fee": "0"},
            {"kind": "fee", "event_id": "fee:p1:close", "symbol": "BTC-USDT", "fee": "0.30"},
        ],
    )

    # --- paper_positions.json: брокерское состояние для сверки ---
    (d / "paper_positions.json").write_text(
        json.dumps(
            {
                "positions": [],
                "realized_pnl": "-1.5",
                "initial_capital": "1000",
                "cooldowns": {},
            }
        ),
        encoding="utf-8",
    )

    # --- htf_shadow_bans.jsonl: 3 запрета ---
    _write(
        d / "htf_shadow_bans.jsonl",
        [
            {"ts": 1_700_000_000_000, "symbol": "BTC-USDT", "bar_time": 1,
             "strategy": "pullback", "direction": "long", "htf_bias": "short"},
            {"ts": 1_700_000_100_000, "symbol": "ETH-USDT", "bar_time": 2,
             "strategy": "pullback", "direction": "long", "htf_bias": "short"},
            {"ts": 1_700_000_200_000, "symbol": "SOL-USDT", "bar_time": 3,
             "strategy": "momentum", "direction": "short", "htf_bias": "long"},
        ],
    )
    return d


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.is_file()}


def test_main_prints_all_sections_and_does_not_touch_state(models_dir, capsys):
    before = _snapshot(models_dir)
    rc = main(["--models-dir", str(models_dir), "--days", "14", "--as-of", AS_OF])
    out = capsys.readouterr().out

    assert rc == 0
    for marker in ("(1) БАКЕТЫ", "(2) АГРЕГАТ KILL-SWITCH", "(3) PAPER_LEDGER",
                   "(4) HTF_SHADOW_BANS", "(5) PAPER_TRADES"):
        assert marker in out, marker

    # (1) новые стратегии: maicross есть бакет (n|ANY=7), остальные — «нет бакетов»
    assert "rounded_top" in out and "НЕТ бакетов" in out
    assert re.search(r"maicross\s+1\s+7\s", out)
    assert "|ANY| двоит" in out
    assert "medianR|paper" in out

    # (2) три вердикта правила
    assert "вырезал бы (и при дедуп. n тоже)" in out
    assert "АВАНСОМ" in out
    assert "не трогает" in out
    assert "владелец смотрит в Actions" in out

    # (3) сверка реплеем: Σfee сходится, realized сходится, инвентарь пуст
    assert "Σfee(replay, только kind=fee) = 0.40000000" in out
    assert "ВЕРДИКТ Σfee: СХОДИТСЯ" in out
    assert "ВЕРДИКТ realized: СХОДИТСЯ" in out
    assert "ВЕРДИКТ инвентарь: СХОДИТСЯ" in out
    assert "дубли event_id (dedup-целостность): 0" in out

    # (4) разбивка банов
    assert "строк: 3" in out
    assert "pullback=2" in out
    assert "long=2 (66.7%)" in out and "short=1" in out

    # (5) окно: стопы и доли
    assert "стоп-выходов (exit_reason содержит 'stop'): 2 (50.0%)" in out
    assert "stop_loss" in out and "tp1" in out
    assert "COOLDOWN-блокировки: логи Actions не видны" in out

    # ТОЛЬКО ЧТЕНИЕ: байты фикстур не изменились, новых файлов нет
    assert _snapshot(models_dir) == before


def test_bucket_math_double_count_and_median(models_dir):
    report = render_report(StateSource(models_dir), days=14, as_of_ms=AS_OF_MS)
    # momentum: бакетов=2, n|ANY=6, n|реж=6, n|все=12 (двойной счёт), PF|ANY=0.5/2.5=0.20
    m = re.search(r"momentum\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)", report)
    assert m is not None and m.groups() == ("2", "6", "6", "12", "0.20")
    # median R из paper_trades: momentum (-1.05, -0.2) -> -0.625; maicross (+0.5) -> +0.500
    assert "-0.625" in report and "+0.500" in report


def test_kill_switch_dedup_numbers(models_dir):
    report = render_report(StateSource(models_dir), days=14, as_of_ms=AS_OF_MS)
    # momentum: n|как=12, PF=0.20, kill; дедуп n=6 — тоже kill
    assert re.search(r"momentum\s+12\s+0\.20\s+ДА\s+6\s+0\.20", report)
    # advance: n|как=6 -> kill авансом; дедуп n=3 -> правило молчит
    assert re.search(r"advance\s+6\s+0\.00\s+ДА\s+3\s+0\.00", report)
    assert "при дедуп. n=3 правило не срабатывает" in report
    # ghost: n=2 -> правило не срабатывает ни в одном варианте
    assert re.search(r"ghost\s+2\s+0\.00\s+нет", report)
    assert "ФАКТИЧЕСКИ вырезанные" in report


def test_ledger_mismatch_is_reported(models_dir):
    trades = (models_dir / "paper_trades.jsonl").read_text(encoding="utf-8")
    extra = {
        "id": "t5", "symbol": "ADA-USDT", "direction": "long",
        "exit_reason": "mae_cut", "strategy": "momentum",
        "pnl": "-0.1", "fees": "0.05", "funding": "0",
        "r_multiple": -0.1, "opened_at": _ms(1.5), "closed_at": _ms(1.0),
    }
    (models_dir / "paper_trades.jsonl").write_text(
        trades + json.dumps(extra, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = render_report(StateSource(models_dir), days=14, as_of_ms=AS_OF_MS)
    assert "ВЕРДИКТ Σfee: РАСХОЖДЕНИЕ -0.05000000" in report


def test_missing_files_are_reported_not_crash(tmp_path, capsys):
    empty = tmp_path / "models"
    empty.mkdir()
    rc = main(["--models-dir", str(empty), "--days", "7", "--as-of", AS_OF])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("НЕТ") >= 5
    assert "strategy_stats.json: НЕТ файла" in out
    assert "paper_ledger.jsonl: НЕТ файла" in out
    assert "htf_shadow_bans.jsonl: НЕТ файла" in out
    assert "Сверка невозможна" in out
    assert not any(empty.iterdir())


def test_days_and_as_of_affect_window(models_dir):
    short = render_report(StateSource(models_dir), days=1, as_of_ms=AS_OF_MS)
    assert "сделок в окне: 0" in short
    long = render_report(StateSource(models_dir), days=14, as_of_ms=AS_OF_MS)
    assert "сделок в окне: 4" in long
    assert "входов/день = 0.29" in long


@pytest.mark.skipif(shutil.which("git") is None, reason="git недоступен")
def test_git_ref_source_is_readonly():
    """--git-ref читает закоммиченное состояние и не трогает рабочую копию."""
    repo_models = Path(__file__).resolve().parents[2] / "models"
    before = _snapshot(repo_models)
    source = StateSource(repo_models, git_ref="HEAD")
    assert source.describe().startswith("git HEAD @")
    stats = source.read_json("strategy_stats.json")
    assert isinstance(stats, dict) and "buckets" in stats
    # файла paper_ledger.jsonl нет в HEAD (и нет на диске) — честный None
    assert source.exists("paper_ledger.jsonl") is False
    assert _snapshot(repo_models) == before


def test_parse_as_of_formats():

    assert _parse_as_of("2026-09-12T06:13Z") == int(
        datetime(2026, 9, 12, 6, 13, tzinfo=UTC).timestamp() * 1000
    )
    assert _parse_as_of("2026-09-12") == int(
        datetime(2026, 9, 12, tzinfo=UTC).timestamp() * 1000
    )
    assert _parse_as_of(None) is None
    assert _parse_as_of("не дата") is None


def test_cli_git_ref_prints_report_without_writing_state(capsys):
    """CLI в режиме --git-ref: отчёт печатается, рабочая копия не меняется."""
    repo_root = Path(__file__).resolve().parents[2]
    if shutil.which("git") is None or not (repo_root / ".git").exists():
        pytest.skip("git-репозиторий недоступен")
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        pytest.skip("HEAD недоступен")
    before = _snapshot(repo_root / "models")
    rc = main(
        ["--models-dir", str(repo_root / "models"), "--git-ref", "HEAD",
         "--days", "14", "--as-of", AS_OF]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "источник: git HEAD @" in out
    assert "(5) PAPER_TRADES.JSONL" in out
    assert _snapshot(repo_root / "models") == before
