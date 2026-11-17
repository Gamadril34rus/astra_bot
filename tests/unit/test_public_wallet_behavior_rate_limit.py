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


def test_wallet_collection_uses_common_window_and_includes_twap_slices(monkeypatch) -> None:
    now_ms = 1_800_000_000_000
    requests = []
    responses = [
        [{
            "coin": "BTC",
            "time": now_ms - 2_000,
            "tid": 1,
            "side": "B",
            "sz": "0.1",
            "px": "50000",
            "startPosition": "0",
            "dir": "Open Long",
        }],
        [{
            "twapId": "twap-1",
            "fill": {
                "coin": "ETH",
                "time": now_ms - 1_000,
                "tid": 2,
                "side": "A",
                "sz": "0.2",
                "px": "3000",
                "startPosition": "0",
                "dir": "Open Short",
            },
        }],
    ]

    def fake_post(payload):
        requests.append(payload)
        return responses.pop(0)

    monkeypatch.setattr(research, "_post_json", fake_post)
    monkeypatch.setattr(research.time, "sleep", lambda _: None)

    result = research.fetch_wallet_fills("0xabc", now_ms, pause_s=0, window_hours=96)

    assert requests[0]["type"] == "userFillsByTime"
    assert requests[1]["type"] == "userTwapSliceFillsByTime"
    assert requests[0]["startTime"] == now_ms - 96 * 60 * 60 * 1000
    assert result["regular_fill_count"] == 1
    assert result["twap_slice_fill_count"] == 1
    assert result["window_complete"] is True
    assert result["fills"][1]["fill_source"] == "twap_slice"
    assert result["fills"][1]["twapId"] == "twap-1"
    assert research.summarize_fills(result["fills"])["fills_by_source"] == {
        "twap_slice": 1,
        "user_fill": 1,
    }
