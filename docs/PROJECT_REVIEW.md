# ASTRA BOT — Professional Project Review

## Review scope

This review covers the trading architecture, ML/data pipeline, risk controls, operations, GitHub deployment, Telegram UX, security posture, documentation and future real-money deployment.

## Priority 0 — do not enable real money

ASTRA must remain Demo-only until the following are independently verified:

- chronological out-of-sample validation;
- positive Demo PnL after fees;
- controlled drawdown;
- stable results across multiple market regimes;
- sufficient trade count and active days;
- model calibration, not only classification accuracy;
- execution quality under real exchange latency and slippage;
- no unresolved operational or reconciliation errors.

No software can guarantee that a real trade will never lose money. The system therefore uses hard risk limits and a manual real-money gate.

## Priority 1 — architecture

### Market understanding

Use one canonical feature engine for historical research, Demo and future production. Add features only through versioned schemas so an old model cannot silently consume a new feature layout.

### Data leakage

All financial ML validation must be chronological. The newest block of data is never used for fitting. Hyperparameter selection should also use rolling walk-forward validation rather than repeatedly tuning against one fixed test block.

### Target definition

The target should represent an executable trade outcome after fees, slippage and a realistic holding horizon. Breakeven and ambiguous TP/SL bars should not be silently classified as losses.

### Model quality

ROC-AUC is not a profitability metric. Track:

- calibration / Brier score;
- precision at the actual entry threshold;
- expected value after fees;
- profit factor on unseen data;
- drawdown;
- turnover;
- performance by regime, asset and strategy.

### Memory

Keep three layers separate:

1. immutable historical lessons;
2. Demo execution lessons;
3. aggregated pattern memory.

A model must never rewrite the historical source of truth. Retraining creates a new model version with provenance.

## Priority 1 — risk

Current Demo safety wrapper adds:

- 50% capital allocation ceiling;
- per-trade risk ceiling;
- position count limit;
- entry circuit breaker;
- 1% daily-loss halt;
- 5% Demo drawdown entry halt;
- model-quality gate before Demo trading;
- persistent readiness metrics.

These are protection mechanisms, not profit guarantees.

## Priority 1 — operations

GitHub Actions is temporary infrastructure. It is suitable for the current Demo phase but should not become the permanent execution environment.

When a VPS is available, move the same worker to Docker/systemd with:

- persistent volume for state;
- health endpoint;
- process watchdog;
- structured logs;
- database-backed trade ledger;
- atomic model promotion;
- independent reporting process.

## Priority 1 — accounting

The next production hardening step is a double-entry-like trade ledger. Every order, fill, fee, partial fill, cancellation and reconciliation event should be stored independently from the ML lesson store.

The ML lesson is an interpretation of a trade. It is not the financial source of truth.

## Priority 2 — strategy research

Expand the research engine around:

- trend following;
- breakout/retest;
- mean reversion;
- volatility expansion/contraction;
- momentum reversal;
- market-regime classification;
- cross-asset confirmation.

Compare them in the same walk-forward framework. Do not assume that adding more indicators automatically improves the strategy.

## Priority 2 — execution

Before real trading, model:

- spread;
- slippage;
- taker/maker fees;
- minimum order size;
- tick size;
- partial fills;
- rejected orders;
- stale candles;
- API rate limits;
- exchange maintenance.

A backtest that ignores execution costs is marketing material, not a trading system.

## Priority 2 — UX / Telegram

The Telegram interface should remain quiet:

- one morning report at 09:00 MSK;
- one exceptional readiness notification;
- one critical safety alert when trading is halted.

Avoid sending model noise, every scan result or every feature calculation to the operator.

## Priority 2 — security / legal

Never commit exchange or Telegram secrets.

OKX permissions should be minimum necessary. Withdrawal permission must remain disabled.

Before real-money operation, document:

- operator responsibility;
- exchange account ownership;
- tax/accounting treatment in the operator's jurisdiction;
- applicable automated-trading and crypto regulations;
- incident response;
- custody and withdrawal policy.

This repository is software, not an investment-advice service. Real-money deployment requires an independent legal and financial review appropriate to the operator's jurisdiction.

## Design direction

ASTRA should present itself as an engineering/research product rather than a "guaranteed AI trader".

Recommended visual language:

- dark quantitative-terminal aesthetic;
- restrained accent color;
- high information density without visual noise;
- clear separation of Research / Demo / Readiness;
- no fake profit dashboards;
- every performance number labelled with period, sample size and whether it is in-sample or out-of-sample.

## Management KPIs

The project should be managed against four groups of KPIs:

| Group | KPIs |
|---|---|
| Research | OOS expectancy, PF, DD, calibration, regime stability |
| Trading | Demo PnL, fees, slippage, turnover, fill quality |
| Reliability | uptime, failed API calls, reconciliation errors, checkpoint recovery |
| Product | report delivery, operator alerts, model promotion history |

## Current conclusion

ASTRA has a credible experimental architecture and a useful market-understanding direction. The highest-value next steps are not adding another hundred indicators; they are improving temporal validation, execution realism, accounting integrity, risk halts and model calibration.

The system should earn the right to trade real money through evidence, not through a calendar date or an impressive backtest.
