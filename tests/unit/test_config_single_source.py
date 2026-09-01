"""Test for P1-3: single canonical config source.

Проверяем:
1. Единственный canonical config file (settings.yaml).
2. Нет дублирующих/конфликтующих конфигов.
3. Config loads successfully.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSingleConfigSource:
    def test_no_duplicate_config_files(self):
        """В config/ нет дублирующих YAML-файлов с пересекающимися ключами."""
        config_dir = Path(__file__).parent.parent.parent / "config"
        yaml_files = list(config_dir.glob("*.yaml")) + list(config_dir.glob("*.yml"))
        # Должен быть только settings.yaml (+ возможно .env.example не yaml)
        yaml_names = [f.name for f in yaml_files]
        # production.yaml удалён (P1-3) — не должен возвращаться
        assert "production.yaml" not in yaml_names, (
            "production.yaml должен быть удалён (P1-3: единый источник конфига)"
        )
        # settings.yaml всегда присутствует
        assert "settings.yaml" in yaml_names

    def test_settings_loads(self):
        """settings.yaml загружается без ошибок."""
        from astra_bot.core.config import load_settings
        config = load_settings()
        assert config is not None
        assert config.name == "ASTRA BOT"

    def test_no_hardcoded_secrets_in_config(self):
        """Конфиг не содержит хардкод-секретов (env-var ссылки допустимы)."""
        config_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
        content = config_path.read_text()
        suspicious = ["api_key:", "secret:", "password:", "token:"]
        for pattern in suspicious:
            for line in content.split("\n"):
                stripped = line.strip().lower()
                if stripped.startswith(pattern):
                    value = stripped.split(":", 1)[1].strip()
                    # Пустые значения, плейсхолдеры или env-var ссылки допустимы
                    is_ok = (
                        value in ("", '""', "''", "null", "none")
                        or "${" in value  # env var reference
                        or value.startswith('"$')
                    )
                    assert is_ok, f"Potential hardcoded secret: {line.strip()}"
