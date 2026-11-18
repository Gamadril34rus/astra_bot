"""Read-only public market-data client; no private API or order methods."""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from .models import BookSnapshot, MarketTrade

class BingXPublicAPIError(RuntimeError):
    """Transport, HTTP or exchange response error."""

@dataclass(frozen=True, slots=True)
class PublicEndpoints:
    bingx_base_url: str = "https://open-api.bingx.com"
    binance_spot_base_url: str = "https://api.binance.com"

class BingXPublicClient:
    def __init__(self, endpoints: PublicEndpoints = PublicEndpoints(),
                 timeout_seconds: float = 8.0):
        self.endpoints = endpoints
        self.timeout_seconds = timeout_seconds

    def _get(self, base_url: str, path: str, params: dict[str, Any], *,
             bingx_envelope: bool = True) -> Any:
        url = f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "ASTRA-BSR-research/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BingXPublicAPIError(f"market-data request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise BingXPublicAPIError(f"invalid API response: {payload!r}")
        if not bingx_envelope:
            return payload
        if payload.get("code") != 0:
            raise BingXPublicAPIError(f"BingX API error: {payload!r}")
        return payload.get("data")

    def order_book(self, symbol: str, limit: int = 100) -> BookSnapshot:
        data = self._get(self.endpoints.bingx_base_url, "/openApi/swap/v2/quote/depth",
                         {"symbol": symbol, "limit": limit})
        timestamp = int(data.get("T") or time.time() * 1000)
        bids = tuple((float(p), float(q)) for p, q in data["bids"])
        asks = tuple((float(p), float(q)) for p, q in data["asks"])
        return BookSnapshot(symbol, timestamp, bids, asks)

    def recent_trades(self, symbol: str, limit: int = 1000) -> list[MarketTrade]:
        data = self._get(self.endpoints.bingx_base_url, "/openApi/swap/v2/quote/trades",
                         {"symbol": symbol, "limit": limit})
        return [MarketTrade(int(r["time"]), float(r["price"]), float(r["qty"]),
                            bool(r["buyerMaker"])) for r in data]

    def premium_index(self, symbol: str) -> dict[str, Any]:
        data = self._get(self.endpoints.bingx_base_url, "/openApi/swap/v2/quote/premiumIndex",
                         {"symbol": symbol})
        if isinstance(data, list):
            data = next((r for r in data if r.get("symbol") == symbol), None)
        if not isinstance(data, dict):
            raise BingXPublicAPIError(f"premiumIndex missing {symbol}")
        return data

    def funding_history(self, symbol: str, limit: int = 1000) -> list[dict[str, Any]]:
        return self._get(self.endpoints.bingx_base_url, "/openApi/swap/v2/quote/fundingRate",
                         {"symbol": symbol, "limit": limit})

    def klines(self, symbol: str, interval: str = "5m", limit: int = 500) -> list[dict[str, float]]:
        rows = self._get(self.endpoints.bingx_base_url, "/openApi/swap/v3/quote/klines",
                         {"symbol": symbol, "interval": interval, "limit": limit})
        # V3 kline rows are positional arrays, not dictionaries.
        return [{
            "open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
            "close_time": int(r[6]), "quote_volume": float(r[7]),
            "trade_count": float(r[8]), "taker_buy_base": float(r[9]),
            "taker_buy_quote": float(r[10]),
        } for r in rows]

    def binance_spot_price(self, base_asset: str, quote_asset: str = "USDT") -> float:
        data = self._get(self.endpoints.binance_spot_base_url, "/api/v3/ticker/price",
                         {"symbol": f"{base_asset}{quote_asset}"}, bingx_envelope=False)
        return float(data["price"])
