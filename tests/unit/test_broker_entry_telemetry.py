"""Телеметрия входа у PaperBroker (PR #78, часть 4).

Смысл правки: поля entry-telemetry перестают зависеть от доброй воли вызывающего
кода. Движок их и так писал, но позиции, открытые в обход движка (реплей-тесты,
ручные прогоны, будущий live-адаптер), приходили в журнал пустыми — на 132 из
180 сделок эпохи-2 декомпозиция P&L была слепой. Здесь проверяем, что брокер
заполняет снимок сам, ничего не изобретая, и что факт фи́лла попадает в ledger.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from astra_bot.core import ledger
from astra_bot.decision.broker import PaperBroker, PaperPosition, _entry_snapshot

ENTRY = Decimal("100")
STOP = Decimal("98")
TAKE = Decimal("104")
FEE = Decimal("0.0005")
SLIP = Decimal("0.001")


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Брокер с фиксированной моделью издержек и перехватом ledger-строк."""
    rows: list[dict] = []
    monkeypatch.setattr(
        ledger, "record_best_effort",
        lambda **kw: rows.append(kw) or {"ok": True, **kw},
        raising=True,
    )
    b = PaperBroker(
        state_path=tmp_path / "positions.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=FEE,
        slippage_pct=SLIP,
        initial_capital=Decimal("2000"),
    )
    b.ledger_rows = rows  # type: ignore[attr-defined]
    return b


def _open(broker, direction: str = "long", **kw):
    return broker.open_position(
        symbol="BTC-USDT",
        direction=direction,
        entry_price=kw.get("entry", ENTRY),
        stop_loss=kw.get("stop", STOP if direction == "long" else Decimal("102")),
        take_profit=kw.get("take", TAKE if direction == "long" else Decimal("94")),
        quantity=Decimal("1"),
        strategy="test",
        **{"notes": kw["notes"]} if "notes" in kw else {},
    )


# ------------------------------------------------------- снимок на входе -------
def test_snapshot_filled_without_any_notes(broker) -> None:
    pos = _open(broker)
    snap = pos.notes
    assert snap["signal_bar_close"] == 100.0
    # эффективный вход = entry*(1+slip) — брокер считает его сам
    assert snap["effective_entry"] == pytest.approx(100.1)
    assert snap["initial_stop"] == 98.0
    assert snap["initial_take"] == pytest.approx(float(pos.take_profits[0]))
    assert snap["stop_pct"] == pytest.approx(0.02)
    # costs_r = 2*(fee+slip)/stop_pct = 2*0.0015/0.02 = 0.15R round-trip
    assert snap["costs_r"] == pytest.approx(0.15, abs=1e-9)
    assert all(isinstance(v, float) for k, v in snap.items() if v is not None), \
        "notes уходят в JSON state как есть — Decimal там падает"
    assert json.loads(json.dumps(snap)) == snap


def test_open_does_not_invent_price_history(broker) -> None:
    """Открытие НЕ заполняет highest/lowest_price.

    Это не забывчивость, а граница телеметрии: exit_plan читает эти поля как
    ценовую историю позиции (правило D1-structural). Посев ценой входа на баре
    открытия менял бы решения по выходу — а часть 4 обязана быть чистой
    телеметрией. (Регрессия поймана полным прогоном: тест тени выходов.)
    """
    pos = _open(broker)
    assert pos.highest_price is None and pos.lowest_price is None


def test_mfe_mae_are_zero_by_definition_not_missing(broker) -> None:
    """Позиция без обновления цены даёт mfe_r/mae_r = 0.0, а не None/исключение."""
    pos = _open(broker)
    r, mfe, mae = broker._r_metrics(pos, pos.quantity, Decimal("0"))
    assert (r, mfe, mae) == (0.0, 0.0, 0.0)


def test_explicit_notes_win(broker) -> None:
    pos = _open(broker, notes={"effective_entry": 99.5, "costs_r": 0.5, "confidence": 0.7})
    assert pos.notes["effective_entry"] == 99.5
    assert pos.notes["costs_r"] == 0.5
    assert pos.notes["confidence"] == 0.7
    # остальное добивается брокером
    assert pos.notes["initial_stop"] == 98.0
    assert pos.notes["signal_bar_close"] == 100.0


def test_short_snapshot_direction_of_costs(broker) -> None:
    pos = _open(broker, "short")
    assert pos.notes["effective_entry"] == pytest.approx(99.9)  # шорту slip в плюс по цене
    assert pos.notes["initial_stop"] == 102.0
    assert pos.notes["stop_pct"] == pytest.approx(0.02)
    assert pos.notes["costs_r"] == pytest.approx(0.15, abs=1e-9)


def test_snapshot_survives_state_round_trip(broker, tmp_path) -> None:
    pos = _open(broker)
    broker.save()
    raw = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    stored = next(p for p in (raw.get("positions") or raw) if p["id"] == pos.id)
    assert stored["notes"]["initial_stop"] == 98.0
    assert stored["notes"]["costs_r"] == pytest.approx(0.15, abs=1e-9)


# --------------------------------------------------------- факт в ledger -------
def test_open_fill_row_carries_real_vs_signal(broker) -> None:
    pos = _open(broker)
    fills = [r for r in broker.ledger_rows if r["kind"] == "fill"]  # type: ignore[index]
    assert fills, "открытие обязано оставить fill-строку"
    meta = fills[0]["meta"]
    assert meta["signal_bar_close"] == pytest.approx(100.0)
    assert meta["fill_price"] == pytest.approx(100.1)
    assert meta["slippage_realized_pct"] == pytest.approx(0.001, abs=1e-12)
    assert meta["slippage_model_pct"] == pytest.approx(0.001)
    assert meta["fee_model_pct"] == pytest.approx(0.0005)
    # paper: факт тождественен модели (движок исполняет сам себя) — с точностью
    # float, потому что realized считается из цены, а не из процента. Это не
    # измерение слиппеджа, а доказательство, что поле заполняется всегда.
    assert meta["slippage_realized_pct"] == pytest.approx(meta["slippage_model_pct"], abs=1e-9)
    assert pos.notes["effective_entry"] == meta["fill_price"]


def test_short_fill_slippage_sign_is_adverse_not_minus_two(broker) -> None:
    """Регрессия: для шорта `(fill/ref)*-1 - 1` давало ≈ −2.0 вместо +0.001."""
    _open(broker, "short")
    meta = next(r for r in broker.ledger_rows if r["kind"] == "fill")["meta"]  # type: ignore[index]
    assert meta["slippage_realized_pct"] == pytest.approx(0.001, abs=1e-12)
    assert meta["slippage_realized_pct"] > -0.5


# ------------------------------------------------- фолбэки для старых позиций --
def test_r_metrics_restores_risk_from_snapshot(broker) -> None:
    pos = PaperPosition(
        id="legacy", symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        quantity=Decimal("1"), stop_loss=Decimal("98"), take_profits=[Decimal("104")],
        risk_distance=Decimal("0"),  # как у позиции, открытой в обход движка
        notes={"initial_stop": 98.0},
        highest_price=Decimal("102"), lowest_price=Decimal("99"),
    )
    r, mfe, mae = broker._r_metrics(pos, Decimal("1"), Decimal("2"))
    assert r == pytest.approx(1.0)  # 2 U прибыли / риск 2.0
    # mfe_r/mae_r — ВЕЛИЧИНЫ отклонения (так и лежат в журнале: 165/180 mae_r>0
    # при убыточных сделках), а не знаковые R.
    assert mfe == pytest.approx(1.0)
    assert mae == pytest.approx(0.5)


def test_r_metrics_still_zero_without_any_risk_info(broker) -> None:
    pos = PaperPosition(
        id="blind", symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        quantity=Decimal("1"), stop_loss=Decimal("98"), take_profits=[Decimal("104")],
        risk_distance=Decimal("0"), notes={},
    )
    assert broker._r_metrics(pos, Decimal("1"), Decimal("2")) == (0.0, 0.0, 0.0)


def test_entry_snapshot_rebuilds_only_what_is_known() -> None:
    pos = PaperPosition(
        id="old", symbol="BTC-USDT", direction="short", entry_price=Decimal("100"),
        quantity=Decimal("1"), stop_loss=Decimal("102"), take_profits=[Decimal("94")],
        notes=None, fill_price=Decimal("99.9"), risk_distance=Decimal("2"),
    )
    snap = _entry_snapshot(pos)
    # эффективный вход = фактическая цена фи́лла; стоп = entry ± риск-расстояние
    assert snap["effective_entry"] == pytest.approx(99.9)
    assert snap["initial_stop"] == pytest.approx(102.0)
    # signal_bar_close знает только движок — изобретать его запрещено
    assert snap["signal_bar_close"] is None
    assert snap["costs_r"] is None


def test_entry_snapshot_does_not_overwrite_real_notes() -> None:
    pos = PaperPosition(
        id="new", symbol="BTC-USDT", direction="long", entry_price=Decimal("100"),
        quantity=Decimal("1"), stop_loss=Decimal("98"), take_profits=[Decimal("104")],
        notes={"effective_entry": 100.25, "initial_stop": 97.5, "signal_bar_close": 100.0},
        fill_price=Decimal("100.1"), risk_distance=Decimal("2"),
    )
    snap = _entry_snapshot(pos)
    assert snap["effective_entry"] == 100.25
    assert snap["initial_stop"] == 97.5
    assert snap["signal_bar_close"] == 100.0
