"""Tests for P2-3: Kill-switch for losing trading contour.

Проверяем:
1. 5+ дней убытков подряд → HALT.
2. Недельный убыток > 3% equity → HALT.
3. HALT переживает перезагрузку (state persistence).
4. Reset работает.
"""

from __future__ import annotations

from astra_bot.core.kill_switch import KillSwitch, KillSwitchConfig


class TestKillSwitchConsecutiveLosses:
    def test_halt_after_consecutive_loss_days(self, tmp_path):
        """5 дней убытков подряд → HALT."""
        ks = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=5),
            state_path=tmp_path / "ks.json",
        )
        equity = 10000.0
        for i in range(5):
            ks.record_daily_pnl(-50.0, equity, day=f"2026-08-{20+i:02d}")

        assert ks.is_halted()
        assert "consecutive" in ks.state.halt_reason.lower()

    def test_no_halt_with_wins(self, tmp_path):
        """Если есть прибыльный день — счётчик сбрасывается."""
        ks = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=5),
            state_path=tmp_path / "ks.json",
        )
        equity = 10000.0
        # 3 loss days
        for i in range(3):
            ks.record_daily_pnl(-50.0, equity, day=f"2026-08-{20+i:02d}")
        # 1 win day — resets counter
        ks.record_daily_pnl(100.0, equity, day="2026-08-23")
        # 2 more loss days — total consecutive = 2, not 5
        for i in range(2):
            ks.record_daily_pnl(-50.0, equity, day=f"2026-08-{24+i:02d}")

        assert not ks.is_halted()
        assert ks.state.consecutive_loss_days == 2

    def test_four_losses_no_halt(self, tmp_path):
        """4 дня убытков (при пороге 5) — HALT не срабатывает."""
        ks = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=5),
            state_path=tmp_path / "ks.json",
        )
        equity = 10000.0
        for i in range(4):
            ks.record_daily_pnl(-50.0, equity, day=f"2026-08-{20+i:02d}")

        assert not ks.is_halted()


class TestKillSwitchWeeklyLoss:
    def test_halt_on_weekly_loss(self, tmp_path):
        """Недельный убыток > 3% → HALT."""
        ks = KillSwitch(
            config=KillSwitchConfig(
                max_weekly_loss_pct=3.0,
                max_consecutive_loss_days=100,  # disable consecutive to test weekly
            ),
            state_path=tmp_path / "ks.json",
        )
        equity = 10000.0
        # 7 days with -50 each = -350 = 3.5% of 10000
        for i in range(7):
            ks.record_daily_pnl(-50.0, equity, day=f"2026-08-{20+i:02d}")

        assert ks.is_halted()
        assert "weekly" in ks.state.halt_reason.lower()

    def test_no_halt_under_threshold(self, tmp_path):
        """Недельный убыток < 3% — HALT не срабатывает."""
        ks = KillSwitch(
            config=KillSwitchConfig(
                max_weekly_loss_pct=3.0,
                max_consecutive_loss_days=100,  # disable consecutive check
            ),
            state_path=tmp_path / "ks.json",
        )
        equity = 10000.0
        # 7 days with -20 each = -140 = 1.4% of 10000
        for i in range(7):
            ks.record_daily_pnl(-20.0, equity, day=f"2026-08-{20+i:02d}")

        assert not ks.is_halted()


class TestKillSwitchPersistence:
    def test_halt_survives_restart(self, tmp_path):
        """HALT переживает перезагрузку."""
        path = tmp_path / "ks.json"
        ks1 = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=3),
            state_path=path,
        )
        for i in range(3):
            ks1.record_daily_pnl(-50.0, 10000.0, day=f"2026-08-{20+i:02d}")
        assert ks1.is_halted()

        # "Restart": new instance reads same state
        ks2 = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=3),
            state_path=path,
        )
        assert ks2.is_halted()

    def test_reset_clears_halt(self, tmp_path):
        """Reset сбрасывает HALT."""
        ks = KillSwitch(
            config=KillSwitchConfig(max_consecutive_loss_days=3),
            state_path=tmp_path / "ks.json",
        )
        for i in range(3):
            ks.record_daily_pnl(-50.0, 10000.0, day=f"2026-08-{20+i:02d}")
        assert ks.is_halted()

        ks.reset()
        assert not ks.is_halted()


class TestKillSwitchDisabled:
    def test_disabled_does_not_halt(self, tmp_path):
        """Kill-switch disabled — не HALT даже при убытках."""
        ks = KillSwitch(
            config=KillSwitchConfig(enabled=False, max_consecutive_loss_days=1),
            state_path=tmp_path / "ks.json",
        )
        ks.record_daily_pnl(-5000.0, 10000.0, day="2026-08-20")  # 50% loss!
        assert not ks.is_halted()
