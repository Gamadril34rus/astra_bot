from __future__ import annotations

import io
from urllib.error import HTTPError

from scripts import public_wallet_behavior_research as research


def test_market_class_separates_native_crypto_perps() -> None:
    assert research.market_class("BTC") == "native_crypto_perp"
    assert research.market_class("SOL") == "native_crypto_perp"
    assert research.market_class("xyz:NVDA") == "non_native_crypto_perp"
    assert research.market_class("io:ANTH") == "non_native_crypto_perp"
    assert research.market_class("@107") == "non_native_crypto_perp"


def test_position_effect_classifies_add_even_when_exchange_dir_says_open() -> None:
    fill = {
        "dir": "Open Long",
        "side": "B",
        "sz": "0.5",
        "startPosition": "1.0",
    }
    assert research.classify_fill(fill) == "add"


def test_info_api_429_retries_with_backoff(monkeypatch) -> None:
    attempts = {"count": 0}
    waits: list[float] = []

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        class Response:
            def __enter__(self):
                return io.BytesIO(b"[]")

            def __exit__(self, *args):
                return False

        return Response()

    monkeypatch.setattr(research, "urlopen", fake_urlopen)
    monkeypatch.setattr(research.time, "sleep", waits.append)
    assert research._post_json({"type": "userFillsByTime"}) == []
    assert attempts["count"] == 2
    assert waits == [10.0]
