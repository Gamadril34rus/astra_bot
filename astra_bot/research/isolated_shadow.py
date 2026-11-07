"""Pure, isolated three-portfolio shadow primitives.

No exchange adapter, broker, live wiring, order API, or project state path.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib, math, uuid
from typing import Any

class ZoneState(StrEnum):
    OUTSIDE_NEAR="OUTSIDE_NEAR"; OUTSIDE_FAR="OUTSIDE_FAR"; IN_ZONE="IN_ZONE"
    EMPTY_ZONES="EMPTY_ZONES"; NO_OPPOSING_ZONE="NO_OPPOSING_ZONE"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True)
class ZoneDecision:
    state: ZoneState
    boundary: float|None=None
    distance_pct: float|None=None
    reason: str|None=None

@dataclass(frozen=True)
class Signal:
    signal_id:str; symbol:str; side:str; strategy:str; formed_at_ms:int
    market_received_at_ms:int; zone_received_at_ms:int; source_closed_bar_ms:int|None
    decision_at_ms:int; predecision_price:float|None; price_source:str
    entry_reference:float; stop_reference:float; target_reference:float|None
    confidence:float|None=None; timeframe:str="4h"; source:str="zeus_live_arm_feed"
    metadata:dict[str,Any]=field(default_factory=dict)

@dataclass(frozen=True)
class VariantDecision:
    variant:str; accepted_by_variant:bool; reason:str; zone:ZoneDecision

def classify_zone(*,side:str,price:float|None,zones:list[dict[str,float]]|None,
 market_received_at_ms:int|None,zone_received_at_ms:int|None,decision_at_ms:int,
 max_age_ms:int=30_000,threshold_pct:float=1.0)->ZoneDecision:
    if side not in {"long","short"} or price is None or not math.isfinite(price) or price<=0:
        return ZoneDecision(ZoneState.UNKNOWN,reason="invalid_side_or_price")
    if market_received_at_ms is None or zone_received_at_ms is None:
        return ZoneDecision(ZoneState.UNKNOWN,reason="missing_receive_timestamp")
    ages=(decision_at_ms-market_received_at_ms,decision_at_ms-zone_received_at_ms)
    if min(ages)<0:return ZoneDecision(ZoneState.UNKNOWN,reason="future_dated_snapshot")
    if max(ages)>max_age_ms:return ZoneDecision(ZoneState.UNKNOWN,reason="stale_snapshot")
    if zones is None:return ZoneDecision(ZoneState.UNKNOWN,reason="zones_unavailable")
    valid=[]
    for z in zones:
        try:
            lo,hi=float(z["lower"]),float(z["upper"])
            if math.isfinite(lo) and math.isfinite(hi) and 0<lo<=hi:valid.append((lo,hi))
        except (KeyError,TypeError,ValueError):continue
    if not valid:return ZoneDecision(ZoneState.EMPTY_ZONES,reason="no_valid_zones")
    if any(lo<=price<=hi for lo,hi in valid):
        return ZoneDecision(ZoneState.IN_ZONE,reason="price_inside_zone")
    if side=="long":
        opposing=[(lo,hi) for lo,hi in valid if lo>price]
        if not opposing:return ZoneDecision(ZoneState.NO_OPPOSING_ZONE,reason="no_zone_above_price")
        boundary=min(lo for lo,_ in opposing)
    else:
        opposing=[(lo,hi) for lo,hi in valid if hi<price]
        if not opposing:return ZoneDecision(ZoneState.NO_OPPOSING_ZONE,reason="no_zone_below_price")
        boundary=max(hi for _,hi in opposing)
    distance=100.0*abs(boundary-price)/price
    state=ZoneState.OUTSIDE_NEAR if distance<=threshold_pct else ZoneState.OUTSIDE_FAR
    return ZoneDecision(state,boundary,distance,"boundary_distance")

def variant_decision(variant:str,signal:Signal,zone:ZoneDecision)->VariantDecision:
    if variant not in {"A","B","C"}:raise ValueError(f"unknown variant {variant}")
    if variant=="B":
        if signal.symbol not in {"BTC-USDT","ETH-USDT","SOL-USDT","HYPE-USDT"}:
            return VariantDecision(variant,False,"B_UNIVERSE_EXCLUDED",zone)
        if signal.strategy=="zeus_channel_boundary" and signal.side!="short":
            return VariantDecision(variant,False,"B_CHANNEL_SHORT_ONLY",zone)
        return VariantDecision(variant,True,"B_RULES_PASS",zone)
    if variant=="C" and zone.state==ZoneState.OUTSIDE_NEAR:
        return VariantDecision(variant,False,"C_OPPOSING_ZONE_WITHIN_1PCT",zone)
    return VariantDecision(variant,True,"BASE_RULES_PASS",zone)

@dataclass
class VirtualPosition:
    position_id:str; order_id:str; attempt_id:str; signal_id:str; symbol:str; side:str
    strategy:str; opened_at_ms:int; entry_price:float; stop_price:float
    target_price:float|None; quantity:float; initial_risk_cash:float
    entry_fee:float; remaining_qty:float; realized_gross:float=0.0
    fees:float=0.0; funding:float=0.0; status:str="OPEN"; exit_reason:str|None=None
    @property
    def net_pnl(self)->float:return self.realized_gross-self.fees+self.funding

@dataclass
class VirtualPortfolio:
    name:str; initial_capital:float=100_000.0; risk_fraction:float=.01
    max_open:int=5; max_exposure_fraction:float=.30; fee_taker:float=.0005
    slippage_bps:float=5.0; max_leverage:float=6.0
    equity_cash:float=field(init=False); positions:dict[str,VirtualPosition]=field(default_factory=dict)
    closed:list[VirtualPosition]=field(default_factory=list); attempts:list[dict[str,Any]]=field(default_factory=list)
    fills:list[dict[str,Any]]=field(default_factory=list); peak_equity:float=field(init=False)
    max_drawdown_pct:float=0.0; _seen_events:set[str]=field(default_factory=set)
    def __post_init__(self):self.equity_cash=float(self.initial_capital);self.peak_equity=self.equity_cash
    def equity(self,marks:dict[str,float]|None=None)->float:
        marks=marks or {}; unreal=sum((marks.get(p.symbol,p.entry_price)-p.entry_price)*p.remaining_qty*(1 if p.side=="long" else -1) for p in self.positions.values())
        return self.equity_cash+unreal
    def _mark(self,marks=None):
        eq=self.equity(marks);self.peak_equity=max(self.peak_equity,eq)
        if self.peak_equity>0:self.max_drawdown_pct=max(self.max_drawdown_pct,(self.peak_equity-eq)/self.peak_equity*100)
    def attempt(self,signal:Signal,decision:VariantDecision,*,execution_price:float|None=None)->dict[str,Any]:
        aid=uuid.uuid4().hex
        row={"attempt_id":aid,"signal_id":signal.signal_id,"variant":self.name,"accepted_by_variant":decision.accepted_by_variant,"reason":decision.reason,"zone_state":decision.zone.state.value,"decision_at_ms":signal.decision_at_ms}
        if not decision.accepted_by_variant:self.attempts.append(row);return row
        if len(self.positions)>=self.max_open:row.update(accepted_by_variant=False,reason="MAX_OPEN");self.attempts.append(row);return row
        if any(p.symbol==signal.symbol for p in self.positions.values()):row.update(accepted_by_variant=False,reason="SYMBOL_ALREADY_OPEN");self.attempts.append(row);return row
        price=execution_price
        if price is None or not math.isfinite(price) or price<=0:row.update(accepted_by_variant=False,reason="UNKNOWN_EXECUTION_PRICE");self.attempts.append(row);return row
        slip=self.slippage_bps/10000;fill=price*(1+slip if signal.side=="long" else 1-slip)
        dist=abs(fill-signal.stop_reference)
        if dist<=0:row.update(accepted_by_variant=False,reason="INVALID_INITIAL_RISK");self.attempts.append(row);return row
        eq=self.equity();risk_cash=eq*self.risk_fraction;qty=risk_cash/dist
        qty=min(qty,eq*self.max_exposure_fraction/fill,eq*self.max_leverage/fill)
        if qty<=0:row.update(accepted_by_variant=False,reason="ZERO_SIZE");self.attempts.append(row);return row
        oid,pid=uuid.uuid4().hex,uuid.uuid4().hex;fee=qty*fill*self.fee_taker
        p=VirtualPosition(pid,oid,aid,signal.signal_id,signal.symbol,signal.side,signal.strategy,signal.decision_at_ms,fill,signal.stop_reference,signal.target_reference,qty,dist*qty,fee,qty,fees=fee)
        self.positions[pid]=p;self.equity_cash-=fee
        self.fills.append({"fill_id":uuid.uuid4().hex,"order_id":oid,"position_id":pid,"attempt_id":aid,"signal_id":signal.signal_id,"type":"ENTRY","price":fill,"quantity":qty,"fee":fee,"ts_ms":signal.decision_at_ms})
        row.update(accepted_by_variant=True,reason="OPENED",order_id=oid,position_id=pid,quantity=qty,fill_price=fill,initial_risk_cash=p.initial_risk_cash)
        self.attempts.append(row);self._mark();return row
    def close(self,position_id:str,*,event_id:str,price:float,quantity:float,ts_ms:int,reason:str,fee_rate:float|None=None,funding:float=0.0)->dict[str,Any]:
        if event_id in self._seen_events:return {"duplicate":True,"event_id":event_id}
        p=self.positions.get(position_id)
        if p is None or quantity<=0 or quantity>p.remaining_qty+1e-12:return {"unknown":True,"event_id":event_id}
        self._seen_events.add(event_id);slip=self.slippage_bps/10000
        px=price*(1-slip if p.side=="long" else 1+slip);qty=min(quantity,p.remaining_qty)
        gross=(px-p.entry_price)*qty*(1 if p.side=="long" else -1);fee=px*qty*(self.fee_taker if fee_rate is None else fee_rate)
        p.realized_gross+=gross;p.fees+=fee;p.funding+=funding;p.remaining_qty-=qty;self.equity_cash+=gross-fee+funding
        fid=hashlib.sha256(event_id.encode()).hexdigest()[:32]
        self.fills.append({"fill_id":fid,"event_id":event_id,"position_id":position_id,"type":"EXIT","price":px,"quantity":qty,"gross_pnl":gross,"fee":fee,"funding":funding,"ts_ms":ts_ms})
        if p.remaining_qty<=1e-12:p.remaining_qty=0;p.status="CLOSED";p.exit_reason=reason;self.closed.append(p);del self.positions[position_id]
        self._mark();return {"fill_id":fid,"position_id":position_id,"net_fill_pnl":gross-fee+funding}
    def metrics(self,marks:dict[str,float]|None=None)->dict[str,Any]:
        wins=[p.net_pnl for p in self.closed if p.net_pnl>0];losses=[-p.net_pnl for p in self.closed if p.net_pnl<0]
        gw,gl=sum(wins),sum(losses);pf=gw/gl if gl else (None if not gw else float("inf"));pnl=sum(p.net_pnl for p in self.closed);best=sorted(wins,reverse=True)
        return {"variant":self.name,"attempts":len(self.attempts),"fills":len(self.fills),"closed_positions":len(self.closed),"open_positions":len(self.positions),"net_closed_pnl":pnl,"equity_including_open_unrealized":self.equity(marks),"max_drawdown_pct_observed":self.max_drawdown_pct,"profit_factor_closed_positions":pf,"R":"UNKNOWN_NOT_IMPLEMENTED","unknown_R_positions":len(self.closed),"pnl_without_best_winner":pnl-best[0] if best else None,"pnl_without_two_best_winners":pnl-sum(best[:2]) if len(best)>=2 else None}
