"""Strict read-only BingX client for COPY_ZEUS.

Only the three explicitly approved GET endpoints are exposed. Signing is delegated
to the existing BingXClient._sign_query; its general-purpose request method is
intentionally not used because it logs signed URLs and supports trading verbs.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import aiohttp

from astra_bot.adapters.bingx.client import BingXClient

BINGX_MAINNET = "https://open-api.bingx.com"
BINGX_VST = "https://open-api-vst.bingx.com"
ENDPOINTS = {
    "positions": "/openApi/swap/v2/user/positions",
    "allOrders": "/openApi/swap/v2/trade/allOrders",
    "income": "/openApi/swap/v2/user/income",
}
RETRYABLE_CODES = {"100410"}


class CopyZeusAPIError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def mask_identifier(value: str | None) -> str:
    """Never return a full credential; show only its final four characters."""
    value = str(value or "")
    return f"***{value[-4:]}" if len(value) >= 4 else "***"


def environment() -> str:
    return "vst" if (os.getenv("COPYZEUS_ENV", "vst").strip().lower() == "vst") else "real"


def base_url_for(env: str | None = None) -> str:
    return BINGX_VST if (env or environment()).lower() == "vst" else BINGX_MAINNET


def archive_directory(root: str = "models", env: str | None = None):
    from pathlib import Path
    return Path(root) / "copy_zeus" / (env or environment())


class CopyZeusClient:
    """GET-only transport which reuses the established BingX signature routine."""

    allowed_paths = frozenset(ENDPOINTS.values())

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        env: str | None = None,
        session: aiohttp.ClientSession | None = None,
        retries: int = 3,
        sleep=asyncio.sleep,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BINGX_API_KEY", "")
        self.api_secret = api_secret if api_secret is not None else os.getenv("BINGX_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise ValueError("BINGX_API_KEY and BINGX_API_SECRET are required")
        self.env = env or environment()
        self.base_url = base_url_for(self.env)
        self._signer = BingXClient({
            "api_key": self.api_key, "api_secret": self.api_secret,
            "base_url": self.base_url, "rate_limit_qps": 1.0,
        })
        self._session = session
        self._owns_session = session is None
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self.retries = max(1, min(int(retries), 3))
        self._sleep = sleep

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _pace(self):
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, 1.0 - (now - self._last_request))
            if delay:
                await self._sleep(delay)
            self._last_request = time.monotonic()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in self.allowed_paths:
            raise PermissionError(f"COPY_ZEUS endpoint is not allowlisted: {path}")
        if self._session is None:
            self._session = aiohttp.ClientSession()
        safe_params = dict(params or {})
        for attempt in range(self.retries):
            await self._pace()
            signed_query = self._signer._sign_query(safe_params)
            url = f"{self.base_url}{path}?{signed_query}"
            try:
                async with self._session.get(
                    url, headers={"X-BX-APIKEY": self.api_key}
                ) as response:
                    status = response.status
                    payload = await response.json()
                code = str(payload.get("code", "0"))
                if status == 429 or code in RETRYABLE_CODES:
                    if attempt + 1 < self.retries:
                        await self._sleep(2 ** attempt)
                        continue
                    raise CopyZeusAPIError(f"retryable BingX response (HTTP {status}, code {code})", code)
                if status >= 400:
                    raise CopyZeusAPIError(f"BingX HTTP error {status}")
                if code not in {"0", "None"}:
                    raise CopyZeusAPIError(f"BingX API error code {code}: {payload.get('msg', '')}", code)
                return payload
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt + 1 >= self.retries:
                    raise CopyZeusAPIError(f"BingX network request failed: {type(exc).__name__}") from exc
                await self._sleep(2 ** attempt)
        raise CopyZeusAPIError("BingX request exhausted retries")

    async def positions(self, limit: int = 5):
        return await self.get(ENDPOINTS["positions"], {"limit": min(max(int(limit), 1), 5)})

    async def all_orders(self, limit: int = 50, **params):
        return await self.get(ENDPOINTS["allOrders"], {**params, "limit": min(max(int(limit), 1), 50)})

    async def income(self, limit: int = 50, **params):
        return await self.get(ENDPOINTS["income"], {**params, "limit": min(max(int(limit), 1), 50)})
