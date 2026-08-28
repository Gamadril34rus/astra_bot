"""
ASTRA BOT — Prometheus-метрики.

Единая точка регистрации счётчиков/гэджей/гистограмм, чтобы их можно было
импортировать из любого модуля без опасности получить дубликаты
(``ValueError: Duplicated timeseries``) при повторной инициализации.
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry(auto_describe=True)

# --- HTTP / API биржи ----------------------------------------------------
HTTP_REQUESTS_TOTAL = Counter(
    "astra_http_requests_total",
    "Total outgoing HTTP requests",
    labelnames=("service", "method", "endpoint", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_LATENCY = Histogram(
    "astra_http_request_duration_seconds",
    "Outgoing HTTP request latency",
    labelnames=("service", "endpoint"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

# --- Ордера / сделки -----------------------------------------------------
ORDERS_PLACED = Counter(
    "astra_orders_placed_total",
    "Total orders placed",
    labelnames=("exchange", "side", "order_type", "strategy"),
    registry=REGISTRY,
)
SIGNALS_GENERATED = Counter(
    "astra_signals_generated_total",
    "Total trading signals generated",
    labelnames=("strategy", "direction", "status"),
    registry=REGISTRY,
)
TRADES_CLOSED = Counter(
    "astra_trades_closed_total",
    "Total closed trades",
    labelnames=("strategy", "outcome"),
    registry=REGISTRY,
)
TRADE_PNL = Counter(
    "astra_trade_pnl_total",
    "Cumulative realized PnL by strategy (in quote currency)",
    labelnames=("strategy",),
    registry=REGISTRY,
)

# --- Риск / счёт ---------------------------------------------------------
RISK_DECISIONS = Counter(
    "astra_risk_decisions_total",
    "Risk engine decisions",
    labelnames=("decision", "reason"),
    registry=REGISTRY,
)
EQUITY = Gauge(
    "astra_account_equity",
    "Current account equity",
    labelnames=("account",),
    registry=REGISTRY,
)
DRAWDOWN_PCT = Gauge(
    "astra_drawdown_percent",
    "Current drawdown percentage",
    registry=REGISTRY,
)
RISK_STATE = Gauge(
    "astra_risk_state",
    "Current risk state (0=NORMAL, 1=REDUCED, 2=DEFENSIVE, 3=STOP, 4=EMERGENCY)",
    registry=REGISTRY,
)
OPEN_POSITIONS = Gauge(
    "astra_open_positions",
    "Number of open positions",
    labelnames=("engine",),
    registry=REGISTRY,
)

# --- Решения / выходы / гипотезы (Этап 7) ---------------------------------
DECISIONS_TOTAL = Counter(
    "astra_decisions_total",
    "Total pipeline decisions by action and reason code",
    labelnames=("action", "reason"),
    registry=REGISTRY,
)
DECISION_LATENCY = Histogram(
    "astra_decision_duration_seconds",
    "Pipeline decision latency (decide)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    registry=REGISTRY,
)
TICK_LATENCY = Histogram(
    "astra_tick_duration_seconds",
    "Full engine tick latency (step)",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)
EXITS_TOTAL = Counter(
    "astra_exits_total",
    "Total position exits by reason (TP/SL/trailing/BE/max_hold/vol/panic)",
    labelnames=("reason",),
    registry=REGISTRY,
)
HYPOTHESES_TOTAL = Gauge(
    "astra_hypotheses_total",
    "Hypotheses by lifecycle status",
    labelnames=("status",),
    registry=REGISTRY,
)
READINESS_SCORE = Gauge(
    "astra_readiness_score",
    "Current readiness score (0-100)",
    registry=REGISTRY,
)
READINESS_READY = Gauge(
    "astra_readiness_ready",
    "1 = readiness gate passed, 0 = not ready",
    registry=REGISTRY,
)

# --- Система -------------------------------------------------------------
SYSTEM_ERRORS = Counter(
    "astra_system_errors_total",
    "System errors by component",
    labelnames=("component", "error_type"),
    registry=REGISTRY,
)


def render_metrics() -> bytes:
    """Вернуть текущее состояние метрик в text-format для Prometheus."""
    return generate_latest(REGISTRY)
