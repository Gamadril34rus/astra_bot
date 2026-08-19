#!/usr/bin/env python3
"""Helpers for bounded, month-by-month ASTRA historical pretraining."""
from __future__ import annotations
import argparse, asyncio, json, logging, os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from astra_bot.adapters.okx import OKXClient
from astra_bot.core.instruments import TRADING_UNIVERSE
from astra_bot.core.logger import setup_logging
from astra_bot.ml.news_features import ASSET_ALIASES, NewsFeatureService, NewsSnapshot, _score_text
from astra_bot.ml.self_play import SelfPlayConfig, SelfPlayEngine
from astra_bot.ml.weekly_learner import train_weekly
from astra_bot.ml.historical_training import OKXRateLimiter, fetch_historical_candles
LOGGER=logging.getLogger("pretrain_5y")

def resample_candles(candles,hours:int):
    from collections import OrderedDict
    from astra_bot.core import models
    buckets: OrderedDict[int,list]=OrderedDict(); bucket_ms=hours*60*60*1000
    for candle in candles: buckets.setdefault((int(candle.open_time)//bucket_ms)*bucket_ms,[]).append(candle)
    out=[]
    for ts,rows in buckets.items():
        if rows: out.append(models.Candle(exchange="okx",symbol=rows[0].symbol,timeframe=f"{hours}h" if hours<24 else "1d",open_time=ts,open=rows[0].open,high=max(r.high for r in rows),low=min(r.low for r in rows),close=rows[-1].close,volume=sum((r.volume for r in rows),Decimal("0")),quote_volume=sum((r.quote_volume for r in rows),Decimal("0")),trades_count=sum(getattr(r,"trades_count",0) or 0 for r in rows)))
    return out

async def _fetch_symbol(client,symbol,days,limiter,end_time_ms=None):
    try:
        bars=await fetch_historical_candles(client=client,symbol=symbol.replace("/","-"),timeframe="1h",lookback_days=days,limiter=limiter,end_time_ms=end_time_ms)
        for bar in bars: bar.symbol=symbol
        LOGGER.info("history %s: %d candles",symbol,len(bars)); return symbol,bars
    except Exception as exc:
        LOGGER.exception("history failed %s: %s",symbol,exc); return symbol,[]

async def fetch_history(days:int,end_time_ms:int|None=None)->dict[str,list]:
    """Fetch one finite window for every symbol through one shared limiter."""
    client=OKXClient({"api_key":"","api_secret":"","sandbox":False,"enabled":True,"rate_limit_qps":1.0}); limiter=OKXRateLimiter(0.9); await client.initialize()
    try:
        results=await asyncio.gather(*[_fetch_symbol(client,symbol,days,limiter,end_time_ms) for symbol in TRADING_UNIVERSE]); return {symbol:bars for symbol,bars in results}
    finally: await client.close()

def _news_query_text(): return "crypto bitcoin ethereum blockchain regulation ETF"

async def build_monthly_news_cache(path:Path,years:int,month_start=None,month_end=None):
    api_key=os.getenv("NEWS_API_KEY","").strip()
    if not api_key: LOGGER.warning("NEWS_API_KEY не задан: news enrichment пропущен"); return
    import aiohttp
    from dateutil.relativedelta import relativedelta
    start=month_start or (datetime.now(tz=UTC)-relativedelta(years=years)); end=month_end or datetime.now(tz=UTC); aliases={a:set(w) for a,w in ASSET_ALIASES.items()}; news=NewsFeatureService(path); timeout=aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        cursor=start
        while cursor<end:
            month_end=min(cursor+relativedelta(months=1),end); params={"q":f'({_news_query_text()})',"from":cursor.isoformat(),"to":month_end.isoformat(),"language":"en","sortBy":"relevancy","pageSize":100}
            try:
                async with session.get("https://newsapi.org/v2/everything",params=params,headers={"X-Api-Key":api_key}) as resp:
                    if resp.status!=200: LOGGER.warning("NewsAPI %s for %s",resp.status,cursor.date()); cursor=month_end; continue
                    data=await resp.json()
            except Exception as exc: LOGGER.warning("NewsAPI error %s: %s",cursor.date(),exc); cursor=month_end; continue
            articles=data.get("articles") or []; global_scores=[]; asset_scores={a:[] for a in aliases}
            for article in articles:
                text=f"{article.get('title','')} {article.get('description','')}".lower(); score=_score_text(text); global_scores.append(score)
                for asset,words in aliases.items():
                    if any(word in text for word in words): asset_scores[asset].append(score)
            gs=NewsSnapshot(sentiment=(sum(global_scores)/len(global_scores)) if global_scores else 0.0,volume=min(1.0,len(global_scores)/100),confidence=min(1.0,len(global_scores)/30),shock=0.0,source="newsapi",articles=len(global_scores)); key=cursor.strftime("%Y-%m")
            for symbol in TRADING_UNIVERSE:
                scores=asset_scores.get(symbol.split("/")[0]) or []; news._cache[f"{symbol}:{key}"]=NewsSnapshot(sentiment=(sum(scores)/len(scores)) if scores else gs.sentiment,volume=min(1.0,len(scores)/30) if scores else gs.volume,confidence=min(1.0,len(scores)/10) if scores else gs.confidence,shock=0.0,source="newsapi",articles=len(scores)).__dict__
            cursor=month_end
    news.save_cache()

if __name__=="__main__":
    raise SystemExit("Используйте scripts/pretrain_research_runtime.py")
