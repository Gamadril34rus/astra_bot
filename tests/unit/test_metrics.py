"""Тесты Prometheus-метрик."""

from astra_bot.core import metrics


def test_render_metrics_returns_prometheus_format():
    metrics.HTTP_REQUESTS_TOTAL.labels(
        service="okx", method="GET", endpoint="/test", status="200"
    ).inc()

    body = metrics.render_metrics().decode("utf-8")

    assert "astra_http_requests_total" in body
    assert 'service="okx"' in body


def test_risk_engine_updates_gauges():
    from decimal import Decimal

    from astra_bot.engines.risk_engine import RiskEngine

    engine = RiskEngine()
    engine.set_capital(Decimal("1000"), Decimal("1000"))

    body = metrics.render_metrics().decode("utf-8")
    assert "astra_account_equity" in body
    assert "astra_drawdown_percent" in body
    assert "astra_risk_state" in body
