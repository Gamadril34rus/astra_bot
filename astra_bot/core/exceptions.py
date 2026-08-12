"""
ASTRA BOT — Исключения системы
"""


class AstraError(Exception):
    """Базовое исключение ASTRA BOT"""


class ConfigurationError(AstraError):
    """Ошибка конфигурации"""


class ValidationError(AstraError):
    """Ошибка валидации"""
    def __init__(self, message: str, field: str = None, value=None):
        super().__init__(message)
        self.field = field
        self.value = value


class RiskError(AstraError):
    """Ошибка risk-движка"""
    def __init__(self, message: str, trade_id: str = None,
                 reason: str = None, current_value: float = None,
                 limit_value: float = None):
        super().__init__(message)
        self.trade_id = trade_id
        self.reason = reason
        self.current_value = current_value
        self.limit_value = limit_value


class ExchangeError(AstraError):
    """Ошибка биржи"""
    def __init__(self, message: str, exchange: str = None,
                 operation: str = None, original_error: Exception = None):
        super().__init__(message)
        self.exchange = exchange
        self.operation = operation
        self.original_error = original_error


class OrderError(AstraError):
    """Ошибка ордера"""
    def __init__(self, message: str, symbol: str = None,
                 order_id: str = None, side: str = None):
        super().__init__(message)
        self.symbol = symbol
        self.order_id = order_id
        self.side = side


class PositionError(AstraError):
    """Ошибка позиции"""


class MarketDataError(AstraError):
    """Ошибка рыночных данных"""
    def __init__(self, message: str, symbol: str = None,
                 timeframe: str = None, reason: str = None):
        super().__init__(message)
        self.symbol = symbol
        self.timeframe = timeframe
        self.reason = reason


class StaleDataError(MarketDataError):
    """Устаревшие рыночные данные"""


class LiquidityError(AstraError):
    """Ошибка ликвидности"""
    def __init__(self, message: str, symbol: str = None,
                 level: str = None, reason: str = None):
        super().__init__(message)
        self.symbol = symbol
        self.level = level  # HIGH, NORMAL, LOW, CRITICAL
        self.reason = reason


class MLModelError(AstraError):
    """Ошибка ML модели"""
    def __init__(self, message: str, model_version: str = None,
                 operation: str = None):
        super().__init__(message)
        self.model_version = model_version
        self.operation = operation


class TrainingError(AstraError):
    """Ошибка обучения"""


class PredictionError(AstraError):
    """Ошибка предсказания"""


class ReconciliationError(AstraError):
    """Ошибка reconciliation"""
    def __init__(self, message: str, mismatch_type: str = None,
                 details: dict = None):
        super().__init__(message)
        self.mismatch_type = mismatch_type
        self.details = details or {}


class SecurityError(AstraError):
    """Ошибка безопасности"""
    def __init__(self, message: str, severity: str = "high",
                 action_taken: str = None):
        super().__init__(message)
        self.severity = severity
        self.action_taken = action_taken


class UnauthorizedAccessError(SecurityError):
    """Неавторизованный доступ"""


class PromptInjectionError(SecurityError):
    """Попытка prompt injection"""
    def __init__(self, message: str, detected_pattern: str = None,
                 source: str = None):
        super().__init__(message, severity="high",
                        action_taken="REJECTED_NEWS_PROCESSING")
        self.detected_pattern = detected_pattern
        self.source = source


class CircuitBreakerError(AstraError):
    """Сработал circuit breaker"""
    def __init__(self, message: str, reason: str = None,
                 component: str = None):
        super().__init__(message)
        self.reason = reason
        self.component = component


class RecoveryModeError(AstraError):
    """Режим восстановления"""


class TradingDisabledError(AstraError):
    """Торговля отключена"""


class MinimumOrderError(ValidationError):
    """Размер ордера ниже минимального"""


class InsufficientFundsError(ValidationError):
    """Недостаточно средств"""


class OrderRejectedError(ExchangeError):
    """Ордер отклонён биржей"""


class PartialFillError(OrderError):
    """Частичное исполнение"""
    def __init__(self, message: str, symbol: str = None,
                 order_id: str = None, filled_qty: float = None,
                 expected_qty: float = None):
        super().__init__(message, symbol, order_id)
        self.filled_qty = filled_qty
        self.expected_qty = expected_qty


class TimeoutError(AstraError):
    """Таймаут"""


class WebSocketError(AstraError):
    """Ошибка WebSocket"""
