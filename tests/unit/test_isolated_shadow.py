from pathlib import Path
import pytest
from astra_bot.research.isolated_shadow import Signal,ZoneState,VirtualPortfolio,classify_zone,variant_decision

def sig(side="long",symbol="BTC-USDT",strategy="zeus_channel_boundary",sid="same"):
 return Signal(sid,symbol,side,strategy,1000,1000,1000,900,1000,100.0,"synthetic",100.0,98.0,104.0)
def zone(side="long",price=100.0,zones=None,age=0):
 return classify_zone(side=side,price=price,zones=zones,market_received_at_ms=10000-age,zone_received_at_ms=10000-age,decision_at_ms=10000)

def test_long_lower_boundary_and_exact_one_percent():
 z=zone(zones=[{"lower":101.0,"upper":103.0}]);assert z.state==ZoneState.OUTSIDE_NEAR and z.boundary==101.0 and z.distance_pct==pytest.approx(1.0)
 assert not variant_decision("C",sig(),z).accepted_by_variant
def test_short_upper_boundary():
 z=zone(side="short",price=100,zones=[{"lower":97,"upper":99}]);assert z.boundary==99 and z.distance_pct==pytest.approx(1)
def test_more_than_one_percent_passes():
 z=zone(zones=[{"lower":101.0001,"upper":103}]);assert z.state==ZoneState.OUTSIDE_FAR and variant_decision("C",sig(),z).accepted_by_variant
def test_zone_states_separate_and_nonblocking_except_near():
 cases=[zone(zones=[{"lower":99,"upper":101}]),zone(zones=[]),zone(zones=[{"lower":95,"upper":98}]),classify_zone(side="long",price=100,zones=None,market_received_at_ms=None,zone_received_at_ms=None,decision_at_ms=10000)]
 expected=[ZoneState.IN_ZONE,ZoneState.EMPTY_ZONES,ZoneState.NO_OPPOSING_ZONE,ZoneState.UNKNOWN]
 for z,s in zip(cases,expected,strict=True):assert z.state==s and variant_decision("C",sig(),z).accepted_by_variant
def test_stale_and_future_are_unknown():
 assert zone(zones=[{"lower":101,"upper":102}],age=30001).state==ZoneState.UNKNOWN
 z=classify_zone(side="long",price=100,zones=[],market_received_at_ms=10001,zone_received_at_ms=10000,decision_at_ms=10000);assert z.state==ZoneState.UNKNOWN
def test_variant_B_rules():
 z=zone(zones=[])
 assert not variant_decision("B",sig(),z).accepted_by_variant
 assert variant_decision("B",sig(side="short"),z).accepted_by_variant
 assert not variant_decision("B",sig(side="short",symbol="INJ-USDT"),z).accepted_by_variant
 assert variant_decision("B",sig(side="long",strategy="zeus_multi_top_breakdown"),z).accepted_by_variant
def test_portfolios_independent_max_open_per_variant():
 a,b,c=[VirtualPortfolio(n,initial_capital=1000,max_open=1) for n in "ABC"];z=zone(zones=[])
 assert a.attempt(sig(),variant_decision("A",sig(),z),execution_price=100)["reason"]=="OPENED"
 assert a.attempt(sig(symbol="ETH-USDT",sid="s2"),variant_decision("A",sig(),z),execution_price=100)["reason"]=="MAX_OPEN"
 assert b.attempt(sig(),variant_decision("B",sig(side="short"),z),execution_price=100)["reason"]=="B_CHANNEL_SHORT_ONLY" or len(b.positions)==1
 assert not c.positions
def test_repeated_signal_id_distinct_attempts():
 p=VirtualPortfolio("A",initial_capital=1000);z=zone(zones=[])
 x=p.attempt(sig(),variant_decision("A",sig(),z),execution_price=100);pos=p.positions[x["position_id"]]
 p.close(pos.position_id,event_id="x1",price=105,quantity=pos.remaining_qty,ts_ms=2000,reason="test")
 y=p.attempt(sig(),variant_decision("A",sig(),z),execution_price=100);assert x["attempt_id"]!=y["attempt_id"] and x["position_id"]!=y["position_id"]
def test_partial_close_idempotency():
 p=VirtualPortfolio("A",initial_capital=1000);z=zone(zones=[]);x=p.attempt(sig(),variant_decision("A",sig(),z),execution_price=100);pos=p.positions[x["position_id"]];q=pos.remaining_qty
 p.close(pos.position_id,event_id="part",price=102,quantity=q/2,ts_ms=2000,reason="partial");cash=p.equity_cash
 assert p.close(pos.position_id,event_id="part",price=102,quantity=q/2,ts_ms=2000,reason="partial")["duplicate"]
 assert p.equity_cash==pytest.approx(cash)
 p.close(pos.position_id,event_id="final",price=103,quantity=p.positions[pos.position_id].remaining_qty,ts_ms=3000,reason="tp")
 assert len(p.closed)==1 and len([f for f in p.fills if f.get("event_id")=="part"])==1
def test_capture_static_no_trading_or_working_paths():
 root=Path(__file__).resolve().parents[2];src=(root/"scripts/zeus_shadow_capture.py").read_text()
 assert ".post(" not in src and ".delete(" not in src and "live_arm_wiring" not in src
 assert "PENDING_PATH" not in src and "zeus_live_journal" not in src and "BINGX_API_SECRET" in src

def test_A_and_C_share_baseline_stream_and_B_can_fall_back_after_long_channel():
 z=zone(zones=[])
 channel_long=sig(side="long")
 multi_short=sig(side="short",strategy="zeus_multi_top_breakdown",sid="multi")
 # A/C retain the live baseline channel signal; B skips the LONG channel and
 # evaluates the unchanged multi-top fallback from the same market snapshot.
 assert variant_decision("A",channel_long,z).accepted_by_variant
 assert variant_decision("C",channel_long,z).accepted_by_variant
 assert not variant_decision("B",channel_long,z).accepted_by_variant
 assert variant_decision("B",multi_short,z).accepted_by_variant
