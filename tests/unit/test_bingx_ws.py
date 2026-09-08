"""Тесты swap-WebSocket BingX: формат подписки и распаковка сообщений."""

import gzip
import json

from astra_bot.adapters.bingx.websocket import BINGX_WS_BASE, BingXWebSocket


def test_ws_url_is_swap_market():
    assert BINGX_WS_BASE == "wss://open-api-swap.bingx.com/swap-market"
    ws = BingXWebSocket({})
    assert ws.url == BINGX_WS_BASE


def test_data_type_mapping():
    ws = BingXWebSocket({})
    assert ws._data_type("candles", "BTC/USDT") == "BTC-USDT@kline_1m"
    assert ws._data_type("candles", "BTC-USDT", interval="5m") == "BTC-USDT@kline_5m"
    assert ws._data_type("orderbook", "BTC-USDT") == "BTC-USDT@depth20"
    assert ws._data_type("trades", "BTC-USDT") == "BTC-USDT@trade"
    assert ws._data_type("ticker", "BTC-USDT") == "BTC-USDT@ticker"
    assert ws._data_type("last_price", "BTC-USDT") == "BTC-USDT@lastPrice"


def test_sub_message_format():
    ws = BingXWebSocket({})
    msg = json.loads(ws._sub_message("candles", "BTC-USDT"))
    assert msg["reqType"] == "sub"
    assert msg["dataType"] == "BTC-USDT@kline_1m"
    assert msg["id"]  # uuid на каждую подписку


def test_decode_gzip_payload():
    payload = {"dataType": "BTC-USDT@kline_1m", "data": {"c": "50000"}}
    raw = gzip.compress(json.dumps(payload).encode("utf-8"))
    decoded = BingXWebSocket._decode_payload(raw)
    assert decoded == payload


def test_decode_ping_and_pong():
    assert BingXWebSocket._decode_payload("Ping") == "Ping"
    assert BingXWebSocket._decode_payload(gzip.compress(b"Ping")) == "Ping"
    assert BingXWebSocket._decode_payload("Pong") is None


def test_decode_text_json():
    decoded = BingXWebSocket._decode_payload('{"dataType": "x", "code": 0}')
    assert decoded == {"dataType": "x", "code": 0}


def test_decode_garbage_returns_none():
    assert BingXWebSocket._decode_payload("not json {{{") is None
    assert BingXWebSocket._decode_payload(b"\x00\x01\x02") is None
