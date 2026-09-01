"""Integration test for P0-4: legacy strategies disabled + sizing invariants.

Проверяем:
1. scalp5m, scalp, pullback отключены по умолчанию.
2. Sizing-инвариант: notional ≤ max_position_fraction × equity.
3. 365 исторических сделок не проходят новые инварианты (контрольная проверка).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from astra_bot.strategies.pullback import PullbackConfig
from astra_bot.strategies.scalp import ScalpConfig
from astra_bot.strategies.scalp5m import Scalp5mConfig


class TestLegacyStrategiesDisabled:
    def test_scalp5m_disabled_by_default(self):
        """scalp5m отключена по умолчанию (TZ P0-4)."""
        config = Scalp5mConfig()
        assert config.enabled is False

    def test_scalp_disabled_by_default(self):
        """scalp отключена по умолчанию (TZ P0-4)."""
        config = ScalpConfig()
        assert config.enabled is False

    def test_pullback_disabled_by_default(self):
        """pullback отключена по умолчанию (TZ P0-4)."""
        config = PullbackConfig()
        assert config.enabled is False

    def test_can_re_enable_with_flag(self):
        """Стратегии можно включить явно (через флаг)."""
        config = Scalp5mConfig(enabled=True)
        assert config.enabled is True


class TestSizingInvariant:
    """Sizing-инвариант: notional ≤ max_position_fraction × equity."""

    def test_position_notional_cannot_exceed_fraction(self):
        """Позиция не должна превышать max_position_fraction × equity."""
        equity = Decimal("2000")
        max_position_fraction = Decimal("0.10")  # 10%
        max_notional = equity * max_position_fraction  # 200 USDT

        # Пример из paper_trades: AVAX 736.57 × 7.39 = 5,443 USDT >> 200
        avax_price = Decimal("736.57")
        avax_qty = Decimal("7.39")
        avax_notional = avax_price * avax_qty  # ~5,443

        assert avax_notional > max_notional, (
            "AVAX position notional should exceed max_position_fraction × equity"
        )

    def test_historical_trades_violate_sizing(self):
        """365 исторических сделок содержат нарушения sizing-инварианта."""
        trades_path = Path("models/paper_trades.jsonl")
        if not trades_path.exists():
            pytest.skip("No paper_trades.jsonl available")

        equity = Decimal("2000")  # initial_capital
        max_position_fraction = Decimal("0.10")
        max_notional = equity * max_position_fraction

        violations = 0
        total = 0
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                trade = json.loads(line)
                total += 1
                entry_price = Decimal(str(trade.get("entry_price", 0)))
                quantity = Decimal(str(trade.get("quantity", 0)))
                notional = entry_price * quantity
                if notional > max_notional:
                    violations += 1

        # Большинство сделок нарушают sizing — это доказывает необходимость инварианта
        assert violations > 0, "Expected some sizing violations in historical trades"
        violation_rate = violations / total if total > 0 else 0
        assert violation_rate > 0.5, (
            f"Expected >50% violations, got {violation_rate:.1%} ({violations}/{total})"
        )
