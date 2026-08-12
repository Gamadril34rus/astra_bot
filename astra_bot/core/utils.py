"""
ASTRA BOT — Утилиты
"""

import logging
import math
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

logger = logging.getLogger(__name__)


def safe_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    """Безопасно преобразовать значение в Decimal"""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        logger.warning(f"Failed to convert {value} to Decimal, using {default}")
        return default


def round_to_precision(value: Decimal, precision: int) -> Decimal:
    """
    Округлить значение до заданной точности.

    Args:
        value: Значение для округления
        precision: Количество знаков после запятой

    Returns:
        Округлённое значение
    """
    if precision < 0:
        precision = 0
    quantizer = Decimal(10) ** -precision
    return value.quantize(quantizer, rounding=ROUND_DOWN)


def round_to_step(value: Decimal, step: Decimal) -> Decimal:
    """
    Округлить значение до ближайшего допустимого шага.

    Args:
        value: Значение для округления
        step: Минимальный шаг

    Returns:
        Округлёное значение
    """
    if step <= 0:
        return value

    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def calculate_position_size(
    risk_amount: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    min_notional: Decimal | None = None,
    min_quantity: Decimal | None = None,
    price_precision: int = 2,
    quantity_precision: int = 4,
) -> tuple[Decimal, str]:
    """
    Рассчитать размер позиции на основе риска.

    Args:
        risk_amount: Допустимый риск в валюте котировки
        entry_price: Цена входа
        stop_price: Цена стоп-лосса
        min_notional: Минимальная номинальная стоимость ордера
        min_quantity: Минимальное количество
        price_precision: Точность цены
        quantity_precision: Точность количества

    Returns:
        Tuple[размер_позиции, причина_отказа]
        Если позиция невозможна, первый элемент 0, второй — причина.
    """
    # Расчёт расстояния до стопа
    stop_distance = abs(entry_price - stop_price)

    if stop_distance <= 0:
        return Decimal("0"), "Stop distance must be positive"

    # Расчёт теоретического размера
    theoretical_size = risk_amount / stop_distance

    # Проверка минимального номинала
    if min_notional and theoretical_size * entry_price < min_notional:
        return Decimal("0"), f"Position notional {theoretical_size * entry_price} below minimum {min_notional}"

    # Проверка минимального количества
    if min_quantity and theoretical_size < min_quantity:
        return Decimal("0"), f"Position size {theoretical_size} below minimum quantity {min_quantity}"

    # Округление до количества
    position_size = round_to_precision(theoretical_size, quantity_precision)

    # Повторная проверка после округления
    if min_notional and position_size * entry_price < min_notional:
        return Decimal("0"), f"Rounded position notional {position_size * entry_price} below minimum {min_notional}"

    if min_quantity and position_size < min_quantity:
        return Decimal("0"), f"Rounded position size {position_size} below minimum quantity {min_quantity}"

    return position_size, ""


def calculate_stop_loss(
    entry_price: Decimal,
    atr: Decimal,
    atr_multiplier: float = 1.5,
    method: str = "atr",
) -> Decimal:
    """
    Рассчитать цену стоп-лосса.

    Args:
        entry_price: Цена входа
        atr: ATR значение
        atr_multiplier: Множитель ATR
        method: Метод расчёта (atr, percentage, structure)

    Returns:
        Цена стоп-лосса
    """
    if method == "atr" and atr > 0:
        stop_distance = atr * Decimal(str(atr_multiplier))
        # Для long позиции стоп ниже, для short — выше
        # Здесь базовый расчёт, направление определяется стратегией
        return entry_price - stop_distance

    elif method == "percentage":
        # Заглушка для процентного метода
        return entry_price * Decimal("0.99")  # 1% стоп

    return entry_price - atr * Decimal(str(atr_multiplier))


def calculate_take_profit_levels(
    entry_price: Decimal,
    stop_loss: Decimal,
    r_multipliers: list = None,
    method: str = "r_multiple",
) -> list:
    """
    Рассчитать уровни тейк-профита.

    Args:
        entry_price: Цена входа
        stop_loss: Цена стоп-лосса
        r_multipliers: Множители R для ТП
        method: Метод расчёта

    Returns:
        Список уровней ТП с R-значениями
    """
    if r_multipliers is None:
        r_multipliers = [1, 2, 3]  # 1R, 2R, 3R

    risk = abs(entry_price - stop_loss)

    levels = []
    for i, multiplier in enumerate(r_multipliers, 1):
        tp_price = entry_price + risk * Decimal(str(multiplier))
        levels.append({
            "level": i,
            "price": tp_price,
            "r_multiple": multiplier,
            "potential_pnl": risk * Decimal(str(multiplier)),
        })

    return levels


def calculate_risk_reward_ratio(
    entry_price: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> float:
    """
    Рассчитать соотношение риск/прибыль.

    Returns:
        R:R отношение (например, 2.0 для 1:2)
    """
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)

    if risk <= 0:
        return 0.0

    return float(reward / risk)


def calculate_expected_value(
    win_probability: float,
    avg_win_r: float,
    avg_loss_r: float = 1.0,
) -> float:
    """
    Рассчитать математическое ожидание сделки.

    Args:
        win_probability: Вероятность победы (0-1)
        avg_win_r: Средний выигрыш в R
        avg_loss_r: Средний проигрыш в R

    Returns:
        EV в R-единицах
    """
    loss_probability = 1 - win_probability
    ev = win_probability * avg_win_r - loss_probability * avg_loss_r
    return ev


def calculate_position_risk(
    position_size: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
) -> Decimal:
    """
    Рассчитать риск позиции в валюте котировки.
    """
    risk_per_unit = abs(entry_price - stop_price)
    return position_size * risk_per_unit


def format_currency(value: Decimal, currency: str = "₽") -> str:
    """Отформатировать значение валюты"""
    if value is None:
        return "0.00"
    return f"{value:.2f} {currency}"


def format_percentage(value: Decimal, decimals: int = 2) -> str:
    """Отформатировать процент"""
    if value is None:
        return "0.00%"
    return f"{float(value):.{decimals}f}%"


def parse_instrument_symbol(symbol: str) -> tuple[str, str]:
    """Разобрать символ инструмента на базовый и котируемый актив"""
    parts = symbol.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol format: {symbol}")
    return parts[0], parts[1]


def is_valid_symbol(symbol: str) -> bool:
    """Проверить валидность символа"""
    if not symbol or "/" not in symbol:
        return False
    base, quote = symbol.split("/")
    if not base or not quote:
        return False
    return True


def time_to_iso(dt: datetime) -> str:
    """Конвертировать datetime в ISO формат"""
    if dt is None:
        return None
    return dt.isoformat()


def iso_to_time(iso_str: str) -> datetime:
    """Конвертировать ISO строку в datetime"""
    if iso_str is None:
        return None
    return datetime.fromisoformat(iso_str)


def get_timebucket(timestamp: datetime, timeframe: str) -> datetime:
    """
    Получить начало таймфрейма для timestamp.

    Args:
        timestamp: Временная метка
        timeframe: Таймфрейм (1m, 5m, 15m, 1h, 4h, 1d)

    Returns:
        Время начала таймфрейма
    """
    if timeframe == "1m":
        return timestamp.replace(second=0, microsecond=0)
    elif timeframe == "5m":
        minute = (timestamp.minute // 5) * 5
        return timestamp.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "15m":
        minute = (timestamp.minute // 15) * 15
        return timestamp.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "1h":
        return timestamp.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        hour = (timestamp.hour // 4) * 4
        return timestamp.replace(hour=hour, minute=0, second=0, microsecond=0)
    elif timeframe == "1d":
        return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unknown timeframe: {timeframe}")


def calculate_timeframe_minutes(timeframe: str) -> int:
    """Рассчитать длительность таймфрейма в минутах"""
    multipliers = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    return multipliers.get(timeframe, 1)


def generate_client_order_id(strategy: str, symbol: str, timestamp: datetime = None) -> str:
    """Сгенерировать client order ID"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    ts = timestamp.strftime("%Y%m%d%H%M%S%f")[:-3]
    base = f"{symbol.replace('/','-')}/{strategy}"
    return f"astra_{base}_{ts}"


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Ограничить значение диапазоном"""
    return max(min_val, min(value, max_val))


def sigmoid(x: float) -> float:
    """Сигмоидальная функция"""
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    else:
        exp_x = math.exp(x)
        return exp_x / (1 + exp_x)


def normalize(value: float, min_val: float, max_val: float) -> float:
    """Нормализовать значение в диапазон [0, 1]"""
    if max_val <= min_val:
        return 0.5
    return (value - min_val) / (max_val - min_val)


def exponential_moving_average(values: list, period: int) -> float | None:
    """Рассчитать EMA"""
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    ema = values[0]

    for i in range(1, len(values)):
        ema = values[i] * k + ema * (1 - k)

    return ema


def simple_moving_average(values: list, period: int) -> float | None:
    """Рассчитать SMA"""
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


def calculate_atr(
    highs: list,
    lows: list,
    closes: list,
    period: int = 14,
) -> float | None:
    """
    Рассчитать ATR (Average True Range).

    Returns:
        ATR значение или None если недостаточно данных
    """
    if len(highs) < period + 1:
        return None

    tr_values = []

    for i in range(1, len(highs)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # Используем SMA для ATR (можно использовать Wilder's smoothing)
    return sum(tr_values[-period:]) / period


def calculate_rsi(closes: list, period: int = 14) -> float | None:
    """
    Рассчитать RSI (Relative Strength Index).

    Returns:
        RSI значение или None
    """
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_bollinger_bands(
    closes: list,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict | None:
    """
    Рассчитать Bollinger Bands.

    Returns:
        Dict с middle, upper, lower или None
    """
    if len(closes) < period:
        return None

    sma = simple_moving_average(closes, period)
    if sma is None:
        return None

    # Расчёт стандартного отклонения
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = math.sqrt(variance)

    return {
        "middle": sma,
        "upper": sma + std_dev * std,
        "lower": sma - std_dev * std,
        "std": std,
        "bandwidth": (2 * std_dev * std) / sma if sma > 0 else 0,
    }
