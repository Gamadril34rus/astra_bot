#!/usr/bin/env python3
"""Exhaustive historical market-learning pass.

Research only: no money and no exchange orders. History is loaded through the
shared conservative OKX limiter and retries, so all instruments share one
serialized request stream instead of creating bursts.
"""
from __future__ import annotations
import argparse, asyncio, json, uuid
from decimal import Decimal
from pathlib import Path
from astra_bot.adapters.okx.client import OKXClient
from astra_bot.core import models
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.ml import self_play as sp
from astra_bot.ml.historical_training import fetch_historical_candles, OKXRateLimiter
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.weekly_learner import train_weekly
from astra_bot.strategies import MeanReversionStrategy, MomentumStrategy, PullbackStrategy
MAX_LESSONS=500_000
MAX_HISTORY_YEARS=25

async def fetch_history() -> dict[str,list[models.Candle]]:
    client=OKXClient({"api_key":"","api_secret":"","sandbox":False,"enabled":True,"rate_limit_qps":1.2})
    await client.initialize(); limiter=OKXRateLimiter()
    try:
        result={}
        for symbol in TRADING_UNIVERSE:
            try:
                bars=await fetch_historical_candles(client,symbol.replace("/","-"),"1h",MAX_HISTORY_YEARS*365,limiter=limiter)
                for bar in bars: bar.symbol=symbol
                print(f"History {symbol}: {len(bars)} candles",flush=True)
                if bars: result[symbol]=bars
            except Exception as exc:
                print(f"History {symbol}: failed: {exc}",flush=True)
        return result
    finally:
        await client.close()

def _lesson_from_signal(signal,strategy,window,future,cross,news):
    features=compute_market_features(window,timeframe=window[-1].timeframe,extra_features=sp._feature_snapshot(strategy,window,cross))
    news_snapshot=news.cached_historical(window[-1].symbol,window[-1].open_time); features.update(news_snapshot.to_features())
    direction=signal.direction.value; entry=Decimal(str(signal.entry_price)); stop=Decimal(str(signal.stop_loss)); take=Decimal(str(signal.take_profit)); exit_price=entry; exit_time=window[-1].open_time
    for bar in future[:48]:
        if direction=="long":
            if bar.low<=stop: exit_price=stop; exit_time=bar.open_time; break
            if bar.high>=take: exit_price=take; exit_time=bar.open_time; break
        else:
            if bar.high>=stop: exit_price=stop; exit_time=bar.open_time; break
            if bar.low<=take: exit_price=take; exit_time=bar.open_time; break
    else:
        if future: last=future[min(47,len(future)-1)]; exit_price=last.close; exit_time=last.open_time
    gross=(exit_price-entry) if direction=="long" else (entry-exit_price); pnl=gross- entry*Decimal("0.0005"); outcome="win" if pnl>0 else "loss" if pnl<0 else "breakeven"; regime=sp._classify_regime(window)
    return {"trade_id":f"hist-{uuid.uuid4()}","symbol":window[-1].symbol,"direction":direction,"entry_time":window[-1].open_time,"exit_time":exit_time,"entry_price":float(entry),"exit_price":float(exit_price),"qty":1.0,"pnl":float(pnl),"pnl_pct":float(pnl/max(entry,Decimal("1e-12"))*100),"outcome":outcome,"strategy":strategy.name,"confidence":float(signal.confidence),"features":{k:float(v) for k,v in features.items()},"market_regime":regime,"news_impulse":abs(float(news_snapshot.shock))>0.5,"news_source":news_snapshot.source,"news_articles":news_snapshot.articles,"influencing_factor":sp._influencing_factor(features,outcome),"counterfactual":sp._counterfactual(outcome,direction,features),"takeaway":f"{window[-1].symbol} {direction.upper()} {outcome}; regime={regime}","recommendation":sp._recommend(outcome,features,direction),"training_phase":"max_available_history_exhaustive_walk_forward","feature_engine":"market_understanding_v1"}

async def run(args)->int:
    history=await fetch_history(); usable={s:b for s,b in history.items() if len(b)>=250}
    if len(usable)<10: raise RuntimeError(f"Недостаточно истории: {len(usable)}/{len(TRADING_UNIVERSE)} инструментов")
    news=NewsFeatureService(Path("models/news_cache.json")); strategies=[PullbackStrategy(),MomentumStrategy(),MeanReversionStrategy()]; lessons=[]; limit=min(args.max_lessons,MAX_LESSONS)
    timestamps=sorted(set.intersection(*(set(c.open_time for c in bars) for bars in usable.values()))); indexes={s:{c.open_time:i for i,c in enumerate(bars)} for s,bars in usable.items()}
    for step,ts in enumerate(timestamps):
        if step<250: continue
        cross={}
        for symbol,bars in usable.items():
            i=indexes[symbol].get(ts)
            if i is not None and i>=1:
                prev=float(bars[i-1].close); curr=float(bars[i].close); cross[f"{symbol}_1h"]=curr/prev-1 if prev else 0.0
        for symbol,bars in usable.items():
            idx=indexes[symbol].get(ts)
            if idx is None or idx<250: continue
            window=bars[:idx+1]; future=bars[idx+1:idx+49]
            if not future: continue
            regime=sp._classify_regime(window)
            for strategy in strategies:
                try: signal=await strategy.evaluate(symbol=symbol,candles=window,current_price=float(window[-1].close),market_regime=regime)
                except Exception: continue
                if not signal or signal.risk_reward_ratio<0.5: continue
                lessons.append(_lesson_from_signal(signal,strategy,window,future,cross,news))
                if len(lessons)>=limit: break
            if len(lessons)>=limit: break
        if len(lessons)>=limit: break
    path=Path("models/lessons.jsonl"); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8") as f:
        for row in lessons: f.write(json.dumps(row,ensure_ascii=False)+"\n")
    from astra_bot.ml.market_memory import MarketMemory
    memory=MarketMemory(Path("models/market_memory.json")); memory.build_from_lessons(path)
    result=train_weekly(lessons_path=path,model_path=Path("models/current.pkl"),min_samples=args.min_samples)
    print(json.dumps({"lessons":len(lessons),"symbols":len(usable),"history_mode":"max_available_per_instrument","max_history_years_safety_bound":MAX_HISTORY_YEARS,"timestamps":len(timestamps),"model_trained":result.trained,"model_message":result.message,"model_auc":result.roc_auc,"model_accuracy":result.accuracy,"memory_patterns":len(memory.data.get("patterns",{}))},ensure_ascii=False,indent=2)); return 0

async def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--max-lessons",type=int,default=500000); p.add_argument("--min-samples",type=int,default=2000); p.add_argument("--with-news",action="store_true"); return await run(p.parse_args())
if __name__=="__main__": raise SystemExit(asyncio.run(main()))
