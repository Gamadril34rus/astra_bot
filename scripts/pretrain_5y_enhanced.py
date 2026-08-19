#!/usr/bin/env python3
"""Research-first pretrain executed as resumable calendar-month stages."""
from __future__ import annotations
import asyncio, json
from datetime import UTC, datetime
from pathlib import Path
import numpy as np
from dateutil.relativedelta import relativedelta
from astra_bot.ml import self_play as sp
from astra_bot.ml.market_memory import MarketMemory
from astra_bot.ml.market_understanding import compute_market_features
from astra_bot.ml.news_features import NewsFeatureService
from astra_bot.ml.research_engine import research_history_v2
from scripts import pretrain_5y
PROGRESS=Path("models/pretrain_progress.json")

def install_enhanced_self_play():
    original_snapshot=sp._feature_snapshot; news_service=NewsFeatureService(Path("models/news_cache.json"))
    def enhanced_snapshot(strategy,candles,cross_snapshot=None):
        base=original_snapshot(strategy,candles,cross_snapshot); enhanced=compute_market_features(candles[-260:],timeframe=getattr(candles[-1],"timeframe","1h"),extra_features=base); enhanced.update(news_service.cached_historical(candles[-1].symbol,candles[-1].open_time).to_features()); return enhanced
    sp._feature_snapshot=enhanced_snapshot
    def dynamic_ml_approves(self,features):
        if self._ml_model is None: return True
        try:
            names=list(getattr(self._ml_model,"feature_names",[]) or [])
            if not names: return True
            vector=np.asarray([[float(features.get(n,0.0)) for n in names]],dtype=float)
            return float(self._ml_model.predict_probability(vector))>=self.config.ml_min_probability
        except Exception: return True
    sp.SelfPlayEngine._ml_approves=dynamic_ml_approves

def load_progress(years:int)->dict:
    if PROGRESS.exists():
        try: return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError): pass
    start=datetime.now(tz=UTC)-relativedelta(years=years); return {"years":years,"next_month":start.strftime("%Y-%m"),"completed_months":[]}

def save_progress(state):
    PROGRESS.parent.mkdir(parents=True,exist_ok=True); PROGRESS.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")

def month_bounds(value:str):
    start=datetime.strptime(value,"%Y-%m").replace(tzinfo=UTC); return start,start+relativedelta(months=1)

def merge_jsonl(pattern:str,target:Path):
    with target.open("w",encoding="utf-8") as out:
        for path in sorted(Path("models").glob(pattern)):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip(): out.write(line+"\n")

async def main()->int:
    pretrain_5y.setup_logging()
    parser=pretrain_5y.argparse.ArgumentParser(description="ASTRA monthly historical pretrain")
    parser.add_argument("--years",type=int,default=5); parser.add_argument("--target-trades",type=int,default=5000); parser.add_argument("--min-samples",type=int,default=2000); parser.add_argument("--capital",type=float,default=10000.0); parser.add_argument("--with-news",action="store_true"); args=parser.parse_args()
    install_enhanced_self_play(); state=load_progress(args.years); month=state["next_month"]; start,end=month_bounds(month); now=datetime.now(tz=UTC)
    if start>=now: print(f"PRETRAIN COMPLETE: {len(state['completed_months'])} months"); return 0
    is_partial=end>now; end=min(end,now); month_days=max(31,(end-start).days); warmup_days=300
    print(f"MONTH START {month}: {start.date()} -> {end.date()} | warmup={warmup_days}d",flush=True)
    history=await pretrain_5y.fetch_history(month_days+warmup_days,end_time_ms=int(end.timestamp()*1000))
    usable={s:bars for s,bars in history.items() if len(bars)>=240}
    if not usable: raise RuntimeError("Не удалось получить достаточную историю за месяц")
    start_ms=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000); month_history={}
    for symbol,bars in usable.items():
        target=[b for b in bars if start_ms<=b.open_time<=end_ms]; warm=[b for b in bars if b.open_time<start_ms][-260:]; month_history[symbol]=warm+target
    news=NewsFeatureService(Path("models/news_cache.json"))
    if args.with_news: await pretrain_5y.build_monthly_news_cache(Path("models/news_cache.json"),args.years,start,end); news=NewsFeatureService(Path("models/news_cache.json"))
    summary={}
    for tf,hours in (("1h",1),("4h",4),("1d",24)):
        tf_history={s:(bars if hours==1 else pretrain_5y.resample_candles(bars,hours)) for s,bars in month_history.items()}
        obs=Path(f"models/research_observations_{month}_{tf}.jsonl"); hyp=Path(f"models/research_hypotheses_{month}_{tf}.json")
        stats=research_history_v2(tf_history,output=obs,hypotheses_output=hyp,sample_every={"1h":6,"4h":1,"1d":1},validation_fraction=0.30,min_samples=30,news_service=news); summary[tf]=stats
        print(f"Research {month} {tf}: symbols={stats['symbols']} observations={stats['observations']} events={stats['events']} oos={stats['validation_observations']}",flush=True)
        cfg=sp.SelfPlayConfig(timeframe=tf,symbols=tuple(tf_history.keys()),initial_capital=__import__("decimal").Decimal(str(args.capital)),target_trades=args.target_trades,position_fraction=__import__("decimal").Decimal("0.05"),ml_min_probability=0.60,lessons_output=Path(f"models/lessons_{month}_{tf}.jsonl"))
        report=await sp.SelfPlayEngine(cfg).run(history=tf_history,append=False)
        print(f"Paper {month} {tf}: trades={report.total_trades} wins={report.wins} losses={report.losses} pnl={report.total_pnl:.2f} drawdown={report.max_drawdown_pct:.2f}%",flush=True)
    merge_jsonl("research_observations_????-??_*.jsonl",Path("models/research_observations.jsonl")); merge_jsonl("lessons_????-??_*.jsonl",Path("models/lessons.jsonl"))
    memory=MarketMemory(); research_count=memory.import_research(Path("models/research_observations.jsonl")); count=memory.build_from_lessons(Path("models/lessons.jsonl")); memory.save()
    result=__import__("astra_bot.ml.weekly_learner",fromlist=["train_weekly"]).train_weekly(lessons_path=Path("models/lessons.jsonl"),model_path=Path("models/current.pkl"),min_samples=args.min_samples)
    Path("models/research_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    state["completed_months"].append(month); state["next_month"]=(start+relativedelta(months=1)).strftime("%Y-%m"); save_progress(state)
    print(f"MONTH COMPLETE {month}: research={research_count} lessons={count}; model={result.message}; next={state['next_month']}; partial={is_partial}",flush=True)
    return 0

if __name__=="__main__": raise SystemExit(asyncio.run(main()))
