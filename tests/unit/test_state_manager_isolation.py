"""Тесты изоляции git-based persistence (ASTRA_STATE_DIR).

Регрессия: прогон тестов затирал продакшн-файлы data/state.json и
data/trades.db, потому что StateManager игнорировал ASTRA_STATE_DIR.
"""

from pathlib import Path

from astra_bot.data.state_manager import StateManager, get_state_manager


class TestStateManagerEnv:
    def test_env_dir_overrides_default(self, tmp_path: Path, monkeypatch):
        state_dir = tmp_path / "iso"
        monkeypatch.setenv("ASTRA_STATE_DIR", str(state_dir))
        sm = StateManager()
        assert sm.data_dir == state_dir
        assert sm.state_file == state_dir / "state.json"
        assert sm.trades_db == state_dir / "trades.db"

    def test_explicit_dir_wins_over_env(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ASTRA_STATE_DIR", str(tmp_path / "env"))
        explicit = tmp_path / "explicit"
        sm = StateManager(data_dir=explicit)
        assert sm.data_dir == explicit

    def test_no_env_uses_data_dir(self, monkeypatch):
        monkeypatch.delenv("ASTRA_STATE_DIR", raising=False)
        sm = StateManager()
        assert sm.data_dir == Path("data")

    def test_get_state_manager_tracks_env_change(self, tmp_path: Path, monkeypatch):
        first = tmp_path / "first"
        second = tmp_path / "second"
        monkeypatch.setenv("ASTRA_STATE_DIR", str(first))
        sm1 = get_state_manager()
        assert sm1.data_dir == first
        # Смена окружения (как между тестами) обязана пересоздать синглтон.
        monkeypatch.setenv("ASTRA_STATE_DIR", str(second))
        sm2 = get_state_manager()
        assert sm2 is not sm1
        assert sm2.data_dir == second

    def test_trades_isolated_from_repo_data(self, tmp_path: Path, monkeypatch):
        """save_trades пишет в ASTRA_STATE_DIR, а не в data/ репозитория."""
        state_dir = tmp_path / "iso"
        monkeypatch.setenv("ASTRA_STATE_DIR", str(state_dir))
        sm = StateManager()
        sm.save_trades(
            [
                {
                    "id": "t1",
                    "symbol": "BTC-USDT",
                    "side": "long",
                    "pnl": 1.5,
                    "closed_at": 0,
                }
            ]
        )
        assert (state_dir / "trades.db").exists()
        repo_db = Path("data/trades.db")
        before = repo_db.stat().st_mtime_ns if repo_db.exists() else None
        sm.save_trades(
            [
                {
                    "id": "t2",
                    "symbol": "BTC-USDT",
                    "side": "long",
                    "pnl": -0.5,
                    "closed_at": 0,
                }
            ]
        )
        after = repo_db.stat().st_mtime_ns if repo_db.exists() else None
        assert before == after, "тестовая сделка попала в продовый data/trades.db"
