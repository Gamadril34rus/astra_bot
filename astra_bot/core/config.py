"""
ASTRA BOT — Конфигурация системы
"""

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Значение по умолчанию вынесено на уровень модуля, чтобы на него можно было
# ссылаться из ``RiskConfig.from_dict`` (у датаклассов нет атрибута класса
# для поля с ``default_factory``).
DEFAULT_DRAWDOWN_ADAPTATION = [
    {"drawdown": Decimal("0"), "risk_multiplier": Decimal("1.0")},
    {"drawdown": Decimal("0.03"), "risk_multiplier": Decimal("0.75")},
    {"drawdown": Decimal("0.05"), "risk_multiplier": Decimal("0.5")},
    {"drawdown": Decimal("0.08"), "risk_multiplier": Decimal("0.0")},
]


def _expand_env(value):
    """Рекурсивно раскрывать ``${VAR}``/``${VAR:-default}`` в значениях конфига."""
    if isinstance(value, str):
        def _replace(match: "re.Match[str]") -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class RiskConfig:
    """Конфигурация risk-параметров"""
    risk_per_trade: Decimal = Decimal("0.004")  # 0.4%
    daily_loss_limit: Decimal = Decimal("0.02")  # 2%
    weekly_loss_limit: Decimal = Decimal("0.04")  # 4%
    soft_drawdown: Decimal = Decimal("0.05")  # 5%
    hard_drawdown: Decimal = Decimal("0.08")  # 8%
    emergency_drawdown: Decimal = Decimal("0.10")  # 10%
    max_exposure_pct: Decimal = Decimal("0.30")  # 30%
    max_open_positions: int = 5

    # Волатильность
    high_volatility_multiplier: Decimal = Decimal("0.5")
    extreme_volatility_threshold: Decimal = Decimal("0.15")
    volatility_lookback: int = 20

    # Корреляция
    correlation_limit: Decimal = Decimal("0.7")

    # Инкременты риска по просадке
    drawdown_adaptation: list = field(
        default_factory=lambda: [dict(tier) for tier in DEFAULT_DRAWDOWN_ADAPTATION]
    )

    @classmethod
    def from_dict(cls, data: dict) -> "RiskConfig":
        """Создание из словаря"""
        return cls(
            risk_per_trade=Decimal(str(data.get("risk_per_trade", "0.004"))),
            daily_loss_limit=Decimal(str(data.get("daily_loss_limit", "0.02"))),
            weekly_loss_limit=Decimal(str(data.get("weekly_loss_limit", "0.04"))),
            soft_drawdown=Decimal(str(data.get("soft_drawdown", "0.05"))),
            hard_drawdown=Decimal(str(data.get("hard_drawdown", "0.08"))),
            emergency_drawdown=Decimal(str(data.get("emergency_drawdown", "0.10"))),
            max_exposure_pct=Decimal(str(data.get("max_exposure_pct", "0.30"))),
            max_open_positions=data.get("max_open_positions", 5),
            high_volatility_multiplier=Decimal(str(data.get("high_volatility_multiplier", "0.5"))),
            extreme_volatility_threshold=Decimal(str(data.get("extreme_volatility_threshold", "0.15"))),
            volatility_lookback=data.get("volatility_lookback", 20),
            correlation_limit=Decimal(str(data.get("correlation_limit", "0.7"))),
            drawdown_adaptation=data.get(
                "drawdown_adaptation",
                [dict(tier) for tier in DEFAULT_DRAWDOWN_ADAPTATION],
            ),
        )


@dataclass
class ExchangeConfig:
    """Конфигурация биржи"""
    name: str
    api_key: str
    api_secret: str
    passphrase: str | None = None
    sandbox: bool = False
    base_url: str | None = None
    enabled: bool = True
    contract_type: str = "spot"  # spot, linear, inverse


@dataclass
class StrategyConfig:
    """Конфигурация стратегии"""
    name: str
    enabled: bool = True
    weight: float = 1.0
    kill_switch_threshold: float | None = None  #Profit Factor threshold
    decay_threshold: float = 1.0  # Если PF ниже — kill switch

    # Специфичные параметры
    parameters: dict = field(default_factory=dict)


@dataclass
class MLConfig:
    """Конфигурация ML"""
    enabled: bool = False
    model_type: str = "lightgbm"  # lightgbm, xgboost
    model_path: str = "models/"
    auto_train: bool = False
    retraining_interval_days: int = 30
    min_training_samples: int = 1000
    train_split: float = 0.7  # 70% train, 30% test
    validation_split: float = 0.15


@dataclass
class TelegramConfig:
    """Конфигурация Telegram"""
    bot_token: str
    allowed_user_ids: list = field(default_factory=list)
    admin_user_ids: list = field(default_factory=list)
    enable_alerts: bool = True
    daily_report_time: str = "09:00"  # Время ежедневного отчёта


@dataclass
class MarketDataConfig:
    """Конфигурация рыночных данных"""
    poll_interval_ms: int = 1000
    websocket_reconnect_delay: int = 5
    stale_data_timeout_seconds: int = 5
    candle_timeframes: list = field(default_factory=lambda: [
        "1m", "5m", "15m", "1h", "4h", "1d"
    ])
    max_candles_cache: int = 10000
    orderbook_depth: int = 20


@dataclass
class DatabaseConfig:
    """Конфигурация базы данных"""
    host: str = "localhost"
    port: int = 5432
    name: str = "astra_bot"
    user: str = ""
    password: str = ""
    pool_size: int = 10


@dataclass
class RedisConfig:
    """Конфигурация Redis"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0


@dataclass
class SystemConfig:
    """Общая конфигурация системы"""
    name: str = "ASTRA BOT"
    version: str = "0.1.0"
    environment: str = "development"  # development, paper, production
    paper_trading: bool = True
    trading_enabled: bool = False  # Только после подтверждения

    # Universe
    instruments: list = field(default_factory=lambda: [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
    ])

    # Стратегии
    strategies: dict = field(default_factory=lambda: {
        "momentum": {"enabled": True, "weight": 1.0},
        "mean_reversion": {"enabled": True, "weight": 1.0},
        "adaptive_grid": {"enabled": False, "weight": 1.0},
    })

    # ML
    ml: MLConfig = field(default_factory=MLConfig)

    # Telegram
    telegram: TelegramConfig | None = None

    # База данных
    database: DatabaseConfig | None = None

    # Redis
    redis: RedisConfig | None = None

    # Рыночные данные
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)

    # Риск
    risk: RiskConfig = field(default_factory=RiskConfig)

    # Биржи
    exchanges: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SystemConfig":
        """Загрузка конфигурации из YAML файла"""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(_expand_env(data))

    @classmethod
    def from_dict(cls, data: dict) -> "SystemConfig":
        """Создание из словаря"""
        config = cls()

        # Системные настройки. Поддерживаем два варианта layout:
        #   1) всё вложено в секцию `system`;
        #   2) плоский YAML, где trading.instruments / trading.strategies
        #      лежат на верхнем уровне (как в config/settings.yaml).
        sys_data = data.get("system", {})
        trading_data = data.get("trading", {})

        config.name = sys_data.get("name", "ASTRA BOT")
        config.version = sys_data.get("version", "0.1.0")
        config.environment = sys_data.get("environment", "development")
        config.paper_trading = sys_data.get("paper_trading", True)
        config.trading_enabled = sys_data.get("trading_enabled", False)
        config.instruments = (
            trading_data.get("instruments")
            or sys_data.get("instruments")
            or config.instruments
        )
        strategies = (
            trading_data.get("strategies")
            or sys_data.get("strategies")
            or config.strategies
        )
        # Нормализуем стратегии: допускается как {name: {enabled, weight}},
        # так и {name: StrategyConfig(...)} — оставляем словарём как было.
        config.strategies = strategies

        # Риск
        if "risk" in data:
            config.risk = RiskConfig.from_dict(data["risk"])

        # ML
        if "ml" in data:
            ml_data = data["ml"]
            config.ml = MLConfig(
                enabled=ml_data.get("enabled", False),
                model_type=ml_data.get("model_type", "lightgbm"),
                model_path=ml_data.get("model_path", "models/"),
                auto_train=ml_data.get("auto_train", False),
                retraining_interval_days=ml_data.get("retraining_interval_days", 30),
                min_training_samples=ml_data.get("min_training_samples", 1000),
                train_split=ml_data.get("train_split", 0.7),
                validation_split=ml_data.get("validation_split", 0.15),
            )

        # Telegram
        if "telegram" in data:
            tg_data = data["telegram"]
            bot_token = tg_data.get("bot_token", "") or ""
            # Если подстановка ${...} не была раскрыта (переменная не
            # задана), считаем Telegram не настроенным.
            if bot_token.startswith("${") or "your-" in bot_token.lower():
                bot_token = ""
            config.telegram = TelegramConfig(
                bot_token=bot_token,
                allowed_user_ids=[
                    uid for uid in tg_data.get("allowed_user_ids", [])
                    if not str(uid).startswith("${")
                ],
                admin_user_ids=[
                    uid for uid in tg_data.get("admin_user_ids", [])
                    if not str(uid).startswith("${")
                ],
                enable_alerts=tg_data.get("enable_alerts", True),
                daily_report_time=tg_data.get("daily_report_time", "09:00"),
            )

        # Database
        if "database" in data:
            db_data = data["database"]
            config.database = DatabaseConfig(
                host=db_data.get("host", "localhost"),
                port=db_data.get("port", 5432),
                name=db_data.get("name", "astra_bot"),
                user=db_data.get("user", ""),
                password=db_data.get("password", ""),
                pool_size=db_data.get("pool_size", 10),
            )

        # Redis
        if "redis" in data:
            rd_data = data["redis"]
            config.redis = RedisConfig(
                host=rd_data.get("host", "localhost"),
                port=rd_data.get("port", 6379),
                db=rd_data.get("db", 0),
            )

        # Market data
        if "market_data" in data:
            md_data = data["market_data"]
            config.market_data = MarketDataConfig(
                poll_interval_ms=md_data.get("poll_interval_ms", 1000),
                websocket_reconnect_delay=md_data.get("websocket_reconnect_delay", 5),
                stale_data_timeout_seconds=md_data.get("stale_data_timeout_seconds", 5),
                candle_timeframes=md_data.get("candle_timeframes",
                    ["1m", "5m", "15m", "1h", "4h", "1d"]),
                max_candles_cache=md_data.get("max_candles_cache", 10000),
                orderbook_depth=md_data.get("orderbook_depth", 20),
            )

        # Exchanges
        if "exchanges" in data:
            for name, ex_data in data["exchanges"].items():
                config.exchanges[name] = ExchangeConfig(
                    name=name,
                    api_key=ex_data.get("api_key", ""),
                    api_secret=ex_data.get("api_secret", ""),
                    passphrase=ex_data.get("passphrase"),
                    sandbox=ex_data.get("sandbox", False),
                    base_url=ex_data.get("base_url"),
                    enabled=ex_data.get("enabled", True),
                    contract_type=ex_data.get("contract_type", "spot"),
                )

        return config


# Глобальный синглтон конфигурации
_settings: SystemConfig | None = None


def load_settings(path: str | Path | None = None) -> SystemConfig:
    """Загрузить конфигурацию из файла"""
    global _settings

    if path is None:
        # Поиск по умолчанию
        config_paths = [
            Path("config/settings.yaml"),
            Path(__file__).parent.parent / "config" / "settings.yaml",
        ]
        for p in config_paths:
            if p.exists():
                path = p
                break

    if path is None:
        raise FileNotFoundError("Configuration file not found")

    _settings = SystemConfig.from_yaml(path)
    return _settings


def get_settings() -> SystemConfig:
    """Получить текущую конфигурацию"""
    global _settings
    if _settings is None:
        raise RuntimeError("Settings not loaded. Call load_settings() first.")
    return _settings


def reset_settings() -> None:
    """Сбросить конфигурацию (для тестов)"""
    global _settings
    _settings = None
