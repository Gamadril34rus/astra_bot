from astra_bot.decision.zone_shadow import is_enabled


def test_zone_shadow_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("ZONE_SHADOW", raising=False)
    assert is_enabled() is False


def test_zone_shadow_flag_enables_only_when_requested():
    assert is_enabled("1") is True
    assert is_enabled("0") is False
