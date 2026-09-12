"""Request-id contextvar for structured logs (TZ P2.3)."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_var.get("-")


def set_request_id(value: str) -> None:
    request_id_var.set(value or "-")
