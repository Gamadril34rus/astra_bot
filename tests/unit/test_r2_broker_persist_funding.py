"""R2-01/R2-02 (внешний аудит 28.09): вотермарка bars_held и каденс фандинга.

R2-01 (КРИТ): ``_last_extremes_bar`` не персистился — процесс пересоздаётся
каждые ~5 мин (CI-сессия), вотермарка терялась, и текущий бар засчитывался
в ``bars_held`` заново на каждый тик. Фикс: вотермарка живёт в state-файле
брокера и восстанавливается при загрузке.

R2-02: фандинг умножал bars_held (который тикает по первичным 5m-барам) на
минуты ``pos.timeframe`` — для 4h-позиции платёж завышался в 48 раз.
Фикс: FUNDING_BAR_MINUTES — длительность первичного бара контура.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from astra_bot.core import models
from astra_bot.decision.broker import FUNDING_BAR_MINUTES, PaperBroker


def _broker(tmp_path, **kw) -> PaperBroker:
    defaults = dict(
        state_path=tmp_path / "pos.json",
        trades_path=tmp_path / "trades.jsonl",
        fee_pct=Decimal("0.001"),
        slippage_pct=Decimal("0"),
    )
    defaults.update(kw)
    return PaperBroker(**defaults)


def _pos(broker: PaperBroker, **kw):
    args = dict(
        symbol="BTC-USDT",
        direction="long",
        entry_price=Decimal("100"),
        stop_loss=Decimal("97"),
        take_profit=Decimal("106"),
        quantity=Decimal("1"),
    )
    args.update(kw)
    return broker.open_position(**args)


def _bar(ts: int, symbol: str = "BTC-USDT") -> models.Candle:
    return models.Candle(
        exchange="test",
        symbol=symbol,
        timeframe="5m",
        open_time=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99.5"),
        close=Decimal("100"),
        volume=Decimal("10"),
        quote_volume=Decimal("1"),
    )


class TestWatermarkPersisted:
    def test_same_bar_not_recounted_after_restart(self, tmp_path):
        """Тот же бар после «перезапуска» не считается в bars_held второй раз."""
        b1 = _broker(tmp_path)
        pos = _pos(b1)
        b1.update_extremes(_bar(1000))
        assert pos.bars_held == 1
        b1.save()

        b2 = _broker(tmp_path)  # «перезапуск»: новый брокер, тот же state
        assert b2._last_extremes_bar == {"BTC-USDT": 1000}
        b2.update_extremes(_bar(1000))  # тот же бар — НЕ считается заново
        assert b2.positions[0].bars_held == 1
        b2.update_extremes(_bar(2000))  # новый бар — считается
        assert b2.positions[0].bars_held == 2

    def test_restart_per_tick_no_longer_inflates(self, tmp_path):
        """Репрессия R2-01: 10 «перезапусков» на одном и том же баре.

        До фикса каждый перезапуск добавлял +1 в bars_held (позиция
        старела на тик вместо бара); после — счётчик стоит на месте.
        """
        b = _broker(tmp_path)
        _pos(b)
        b.update_extremes(_bar(1000))
        for _ in range(10):
            b.save()
            b = _broker(tmp_path)
            b.update_extremes(_bar(1000))
        assert b.positions[0].bars_held == 1

    def test_state_without_key_still_loads(self, tmp_path):
        """Старый state-файл без ключа last_extremes_bar загружается."""
        b = _broker(tmp_path)
        _pos(b, timeframe="4h")
        b.save()
        data = json.loads((tmp_path / "pos.json").read_text())
        assert "last_extremes_bar" in data
        data.pop("last_extremes_bar")
        (tmp_path / "pos.json").write_text(json.dumps(data))

        b2 = _broker(tmp_path)
        assert len(b2.positions) == 1
        assert b2._last_extremes_bar == {}


class TestFundingCadence:
    def test_four_hour_position_one_interval(self, tmp_path):
        """96 пяти-минутных баров = 8 часов = ровно 1 интервал (а не 48)."""
        b = _broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = _pos(b, timeframe="4h")
        pos.bars_held = 96
        assert b._funding_intervals(pos) == Decimal("1")

    def test_timeframe_does_not_change_payment(self, tmp_path):
        """R2-02-регрессия: timeframe позиции больше не влияет на платёж."""
        pays = []
        for tf in ("", "1h", "4h"):
            b = _broker(tmp_path, funding_rate=Decimal("0.0001"))
            pos = _pos(b, timeframe=tf)
            pos.bars_held = 96
            pays.append(b.funding_payment(pos, Decimal("1"), Decimal("100")))
        assert pays[0] == pays[1] == pays[2]
        # notional 100 x ставка 0.0001 x ровно 1 интервал = 0.01
        assert float(pays[0]) == pytest.approx(0.01, abs=1e-9)

    def test_funding_payment_scales_with_bars_held(self, tmp_path):
        """Платёж растёт линейно по барам: 48 баров = половина интервала."""
        b = _broker(tmp_path, funding_rate=Decimal("0.0001"))
        pos = _pos(b, timeframe="4h")
        pos.bars_held = 48
        half = b.funding_payment(pos, Decimal("1"), Decimal("100"))
        assert float(half) == pytest.approx(0.005, abs=1e-9)

    def test_module_constant_is_primary_bar(self):
        # Длительность первичного бара обоих контуров (5m).
        assert FUNDING_BAR_MINUTES == 5
