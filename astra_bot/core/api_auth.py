"""HTTP API key gate for mutating / sensitive FastAPI endpoints.

Policy (TZ P0.1):
- Open: ``/``, ``/health``, ``/ping``, Telegram webhook.
- Protected: ``/tick``, ``/train``, ``/self_play``, ``/retrain``,
  ``/status``, ``/metrics``.
- If ``ASTRA_API_KEY`` is set — header ``X-API-Key`` (or
  ``Authorization: Bearer``) must match (constant-time compare).
- If the key is unset: paper/development is allowed (local/CI);
  ``ENVIRONMENT=production|prod|live`` → 401 (fail-closed).
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

OPEN_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/ping",
    }
)
OPEN_PREFIXES: tuple[str, ...] = ("/telegram/webhook",)
PROTECTED_PREFIXES: tuple[str, ...] = (
    "/tick",
    "/train",
    "/self_play",
    "/retrain",
    "/status",
    "/metrics",
)

_PROD_ENVS = frozenset({"production", "prod", "live"})


def configured_api_key() -> str:
    return (os.environ.get("ASTRA_API_KEY") or "").strip()


def runtime_environment() -> str:
    return (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("ASTRA_ENV")
        or "paper"
    ).strip().lower()


def is_production() -> bool:
    return runtime_environment() in _PROD_ENVS


def is_open_path(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean in OPEN_PATHS:
        return True
    return any(clean.startswith(p) for p in OPEN_PREFIXES)


def is_protected_path(path: str) -> bool:
    if is_open_path(path):
        return False
    clean = path.rstrip("/") or "/"
    return any(clean == p or clean.startswith(p + "/") for p in PROTECTED_PREFIXES)


def _extract_presented_key(headers: Iterable[tuple[bytes, bytes]] | dict[str, str]) -> str:
    if isinstance(headers, dict):
        raw = headers.get("x-api-key") or headers.get("X-API-Key") or ""
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if raw:
            return raw.strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""
    api_key = ""
    bearer = ""
    for name, value in headers:
        key = name.decode("latin-1").lower()
        val = value.decode("latin-1")
        if key == "x-api-key":
            api_key = val.strip()
        elif key == "authorization" and val.lower().startswith("bearer "):
            bearer = val[7:].strip()
    return api_key or bearer


def keys_match(presented: str, expected: str) -> bool:
    if not expected:
        return False
    # hmac.compare_digest requires equal length; pad-safe compare via digest.
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")) if len(
        presented
    ) == len(expected) else False


def authorize(
    *,
    path: str,
    presented_key: str = "",
    expected_key: str | None = None,
    environment: str | None = None,
) -> tuple[int, str] | None:
    """Return ``(status, detail)`` if the request must be rejected, else None."""
    if not is_protected_path(path):
        return None
    expected = configured_api_key() if expected_key is None else expected_key.strip()
    env = (environment or runtime_environment()).strip().lower()
    if not expected:
        if env in _PROD_ENVS:
            return 401, "ASTRA_API_KEY is required in production"
        return None
    if keys_match(presented_key.strip(), expected):
        return None
    return 401, "Invalid or missing API key"


def authorize_request(request: Request) -> tuple[int, str] | None:
    presented = _extract_presented_key(request.headers.items())
    return authorize(path=request.url.path, presented_key=presented)


class ApiKeyMiddleware:
    """ASGI middleware: 401 on protected paths without a valid key."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        presented = _extract_presented_key(scope.get("headers") or [])
        rejected = authorize(path=path, presented_key=presented)
        if rejected is None:
            await self.app(scope, receive, send)
            return
        status, detail = rejected
        response: Response = JSONResponse({"detail": detail}, status_code=status)
        await response(scope, receive, send)
