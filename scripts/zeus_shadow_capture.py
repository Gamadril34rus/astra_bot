#!/usr/bin/env python3
"""Opt-in, one-shot BingX public-candle capture. Never run during PR preparation."""
from __future__ import annotations
import argparse, asyncio, json, os, sys, time
from pathlib import Path
from decimal import Decimal
import aiohttp

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
BASE="https://open-api.bingx.com"
KLINES="/openApi/swap/v3/quote/klines"
SYMBOLS=("BTC-USDT","ETH-USDT","SOL-USDT","HYPE-USDT","INJ-USDT")
MAX_CALLS_PER_RUN=10

def output_dir(raw:str)->Path:
    if not raw: raise ValueError("--data-dir required")
    p=Path(raw).expanduser().resolve()
    if p==ROOT or ROOT in p.parents: raise ValueError("output must be outside repository")
    return p

async def get_public(session,path,params):
    async with session.get(BASE+path,params=params) as r:
        body=await r.json(); received=int(time.time()*1000)
        if r.status!=200 or not isinstance(body,dict) or body.get("code",0) not in (0,"0",None):
            raise RuntimeError(f"public GET failed: http={r.status}, code={body.get('code') if isinstance(body,dict) else 'invalid'}")
        return body.get("data"),received,r.headers.get("Date")

def parse_candles(raw,symbol,tf):
    from astra_bot.core.models import Candle
    if isinstance(raw,dict): raw=raw.get("klines",raw.get("data",[]))
    out=[]
    for row in raw or []:
        try:
            if isinstance(row,dict):
                vals=(row.get("time") or row.get("openTime"),row["open"],row["high"],row["low"],row["close"],row["volume"],row.get("quoteVolume",0))
            else: vals=(row[0],row[1],row[2],row[3],row[4],row[5],row[7] if len(row)>7 else 0)
            out.append(Candle(exchange="bingx",symbol=symbol,timeframe=tf,open_time=int(vals[0]),
                open=Decimal(str(vals[1])),high=Decimal(str(vals[2])),low=Decimal(str(vals[3])),
                close=Decimal(str(vals[4])),volume=Decimal(str(vals[5])),quote_volume=Decimal(str(vals[6]))))
        except (KeyError,IndexError,TypeError,ValueError): continue
    return sorted(out,key=lambda c:c.open_time)

def channel_config():
    # Frozen copy of live_arm_feed.paper_channel_config() at base SHA e2cd5d1.
    from astra_bot.strategies.zeus_channel_boundary import ZeusChannelBoundaryConfig
    return ZeusChannelBoundaryConfig(enabled=True,lookback=48,min_width_pct=.01,max_width_pct=.12,
        near_boundary_width_frac=.20,use_parallel_clone=True,parallel_tol=.25,min_rr=2.0,
        enable_breakout_hold=True,enable_breakout_retest=True,retest_atr_mult=.15,
        min_hold_bars=2,max_hold_bars=5,min_stop_pct=.01,require_htf_bias=True,
        tp1_rr=1.5,tp2_rr=2.0,tp1_fraction=.4,tp2_fraction=.6)

async def capture_symbol(session,symbol):
    from astra_bot.decision.zone_shadow import build_volume_zones
    from astra_bot.strategies.zeus_channel_boundary import ZeusChannelBoundaryStrategy
    from astra_bot.strategies.zeus_multi_top_breakdown import detect_breakdown_from_ohlc,series_from_candles
    raw4,recv4,date4=await get_public(session,KLINES,{"symbol":symbol,"interval":"4h","limit":120})
    raw1,recv1,date1=await get_public(session,KLINES,{"symbol":symbol,"interval":"1h","limit":120})
    bars4=parse_candles(raw4,symbol,"4h"); bars1=parse_candles(raw1,symbol,"1h")
    if len(bars4)<48 or len(bars1)<30: raise RuntimeError("insufficient_history")
    now=int(time.time()*1000)
    # Match current live feed's forming-bar close as the predecision price.
    price=float(bars4[-1].close)
    zones=build_volume_zones(bars4,price=price) # function excludes final forming bar
    zone_bar=bars4[-2].open_time
    strat=ZeusChannelBoundaryStrategy(channel_config())
    sig=await strat.evaluate(symbol,bars4) # live scan_symbol uses forming 4h candle close
    channel_candidate=None
    if sig is not None:
        side=str(getattr(getattr(sig,"direction",None),"value",getattr(sig,"direction",""))).lower()
        channel_candidate={"signal_id":f"zeus_channel_boundary|{symbol}|{side}|{bars4[-1].open_time}",
            "symbol":symbol,"side":side,"strategy":"zeus_channel_boundary",
            "entry_reference":float(sig.entry_price),"stop_reference":float(sig.stop_loss),
            "target_reference":float(sig.take_profit) if sig.take_profit is not None else None,
            "confidence":float(sig.confidence),"timeframe":"4h","formed_at_ms":bars4[-1].open_time}
    multi_candidate=None
    for tf,bars in (("4h",bars4),("1h",bars1)):
        hi,lo,cl=series_from_candles(bars); br=detect_breakdown_from_ohlc(hi,lo,cl)
        if br is not None:
            multi_candidate={"signal_id":f"zeus_multi_top_breakdown|{symbol}|{br.side}|{bars[-1].open_time}",
                "symbol":symbol,"side":br.side,"strategy":"zeus_multi_top_breakdown",
                "entry_reference":br.entry,"stop_reference":br.stop,"target_reference":br.take_profit,
                "confidence":br.confidence,"timeframe":tf,"formed_at_ms":bars[-1].open_time}
            break
    # Preserve all raw detector candidates first. Each strategy variant then
    # applies its own live-path precedence before portfolio constraints.
    candidates=[x for x in (channel_candidate,multi_candidate) if x is not None]
    selected={
        "A":[channel_candidate or multi_candidate] if (channel_candidate or multi_candidate) else [],
        "B":[(channel_candidate if channel_candidate and channel_candidate["side"]=="short" else multi_candidate)]
            if (channel_candidate and channel_candidate["side"]=="short") or multi_candidate else [],
        "C":[channel_candidate or multi_candidate] if (channel_candidate or multi_candidate) else [],
    }
    selected={k:[x for x in v if x is not None] for k,v in selected.items()}
    from astra_bot.research.isolated_shadow import Signal as ShadowSignal, classify_zone, variant_decision
    annotated=[]
    for raw_sig in candidates:
        shadow_sig=ShadowSignal(signal_id=raw_sig["signal_id"],symbol=symbol,side=raw_sig["side"],
            strategy=raw_sig["strategy"],formed_at_ms=raw_sig["formed_at_ms"],
            market_received_at_ms=max(recv4,recv1),zone_received_at_ms=recv4,
            source_closed_bar_ms=zone_bar,decision_at_ms=now,predecision_price=price,
            price_source="BingX public 4h forming-candle close",
            entry_reference=raw_sig["entry_reference"],stop_reference=raw_sig["stop_reference"],
            target_reference=raw_sig["target_reference"],confidence=raw_sig["confidence"],
            timeframe=raw_sig["timeframe"])
        zd=classify_zone(side=shadow_sig.side,price=price,zones=zones,
            market_received_at_ms=shadow_sig.market_received_at_ms,
            zone_received_at_ms=shadow_sig.zone_received_at_ms,decision_at_ms=now)
        variants={v:{"accepted":variant_decision(v,shadow_sig,zd).accepted_by_variant,
                     "reason":variant_decision(v,shadow_sig,zd).reason,
                     "zone_state":zd.state.value,"boundary":zd.boundary,
                     "distance_pct":zd.distance_pct} for v in ("A","B","C")}
        annotated.append({**raw_sig,"variants":variants})
    by_id={x["signal_id"]:x for x in annotated}
    selected={k:[by_id[x["signal_id"]] for x in v] for k,v in selected.items()}
    return {"symbol":symbol,"market_received_at_ms":max(recv4,recv1),
        "zone_received_at_ms":recv4,"decision_at_ms":now,"predecision_price":price,
        "price_source":"BingX public 4h forming-candle close (same price input as live scan)",
        "source_closed_bar_ms":zone_bar,"zone_source":"closed 4h candles via build_volume_zones",
        "zones":zones,"candidate_signals":annotated,"variant_signals":selected,"request_count":2,"server_date_headers":[date4,date1]}

async def run(args):
    if os.environ.get("BINGX_API_KEY") or os.environ.get("BINGX_API_SECRET"):
        raise RuntimeError("refusing: BingX trading credentials present in environment")
    dest=output_dir(args.data_dir); dest.mkdir(parents=True,exist_ok=True)
    timeout=aiohttp.ClientTimeout(total=15); connector=aiohttp.TCPConnector(limit=2)
    async with aiohttp.ClientSession(timeout=timeout,connector=connector,
            headers={"User-Agent":"ASTRA-isolated-shadow/0.1"}) as session:
        rows=[]
        for symbol in SYMBOLS: rows.append(await capture_symbol(session,symbol))
    count=sum(x["request_count"] for x in rows)
    if count>MAX_CALLS_PER_RUN: raise RuntimeError("public request budget exceeded")
    event={"schema_version":1,"capture_id":f"capture-{int(time.time()*1000)}",
        "captured_at_ms":int(time.time()*1000),"request_count":count,"snapshots":rows}
    path=dest/"market_capture.jsonl"
    with path.open("a",encoding="utf-8") as f:f.write(json.dumps(event,separators=(",",":"),sort_keys=True)+"\n")
    print(json.dumps({"capture_id":event["capture_id"],"symbols":len(rows),"requests":count,"output":str(path)}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--data-dir",required=True)
    asyncio.run(run(p.parse_args()))
if __name__=="__main__":main()
