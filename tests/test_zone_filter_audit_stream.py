


def test_zone_filter_audit_stream_writes_jsonl(tmp_path, monkeypatch):
    import json

    import astra_bot.decision.live_arm_feed as feed

    path = tmp_path / "zone_trade_filter.jsonl"
    monkeypatch.setattr(feed, "ZONE_FILTER_JOURNAL_PATH", path)
    event = {"event": "zone_trade_filter", "signal_id": "test", "accepted": True}
    feed._append_zone_trade_filter_event(event)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == event
    assert path.read_text(encoding="utf-8").endswith("\\n")

