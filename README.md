# ASTRA BOT

### Adaptive research & demo-trading platform for cryptocurrency markets

ASTRA is an experimental **market-aware quantitative trading system** built around one core idea:

> **Learn from as much historical market context as the data source can provide, verify decisions out-of-sample, accumulate explicit lessons, and only then consider real capital.**

The project is currently designed to run **without real money**. Historical research is simulated, and the live worker uses **OKX Demo Trading** only.

---

## 🧠 What ASTRA actually studies

ASTRA does not rely on one indicator or one strategy. It converts the chart into a structured feature space and learns combinations of market conditions.

### Market / chart understanding

- 🕯️ Candlestick geometry and patterns
- 📈 Trend direction and multi-horizon momentum
- 🔺 HH / HL / LH / LL market structure
- 🧱 Support / resistance and pivot levels
- 📐 Trend lines and regression channels
- 🚀 Breakouts and retests
- 📏 Fibonacci retracement context
- 📊 Volume, volume anomalies, OBV and VWAP
- 📉 RSI, Stochastic, CCI, ATR, ADX-like trend strength
- 📐 EMA stacks and Bollinger Bands
- 🔀 Cross-market / major-asset context
- 📰 News sentiment and news shock context when historical data is available
- 📚 Historical pattern memory derived from previous outcomes
- 📖 Counterfactual lessons: what should have been done differently after a losing setup

The feature engine is designed so that **the same market representation is used during historical research and during Demo Trading**. This avoids training a model on one set of signals and deploying it with another.

---

## 🔬 Learning pipeline

```text
Historical market data
        │
        ├── candles / volume
        ├── chart structure
        ├── indicators
        ├── levels / channels
        ├── cross-market context
        └── news context
        │
        ▼
Walk-forward historical simulation
        │
        ▼
Virtual trades / labelled lessons
        │
        ├── outcome
        ├── PnL
        ├── influencing factors
        ├── counterfactual
        └── recommendation
        │
        ▼
Pattern Memory + ML dataset
        │
        ▼
Temporal / out-of-sample model validation
        │
        ▼
Current ML model
        │
        ▼
OKX Demo Trading
        │
        ▼
New lessons from Demo
        │
        ▼
Periodic retraining
```

Historical learning is **research-only**. It does not place exchange orders and does not consume a trading balance.

---

## 🌍 Historical coverage

ASTRA is configured to use a broad universe of liquid crypto assets. The current target universe contains **35 USDT pairs** and is filtered against instruments actually available on OKX.

The historical learner is designed for **MAX_HISTORY** mode rather than an arbitrary fixed five-year window:

- fetch the oldest data the source can actually provide;
- use each asset's own first available date;
- continue pagination until the source stops returning older candles;
- never invent missing history;
- use the longest available history for older assets and shorter history for newer listings.

The exact depth therefore depends on the instrument and on the historical data source.

---

## 📚 Explicit memory, not just a model file

ASTRA keeps two different kinds of memory.

### 1. Lesson memory

Every closed historical or Demo trade can become a structured lesson containing:

- entry / exit context;
- market regime;
- feature vector;
- outcome and PnL;
- influencing factor;
- counterfactual decision;
- recommendation for future setups.

### 2. Pattern memory

`models/market_memory.json` aggregates recurring combinations of chart conditions and stores:

- number of observations;
- wins / losses;
- smoothed historical win rate;
- cumulative PnL;
- recurring recommendations.

This gives the system a second signal layer such as:

```text
similar pattern seen: 148 times
smoothed historical win rate: 67%
historical PnL: positive
common failure mode: high volatility + weak retest
```

A single lucky trade is deliberately prevented from dominating memory through smoothing and minimum-observation logic.

---

## 🧪 Historical research mode

The historical pass is intentionally **capital-free**.

There is no artificial `$10,000 account` limiting how much historical knowledge ASTRA can collect. A virtual position is only a labelling device used to measure what happened after a hypothetical signal.

The exhaustive learner can generate a large number of lessons across:

- the full configured universe;
- every valid historical timestamp;
- multiple strategy families;
- multiple market regimes;
- different chart structures and indicator states.

Current technical storage cap for one exhaustive pass: **up to 500,000 labelled lessons**.

That cap is an infrastructure/storage safeguard, not a trading limit.

---

## 🤖 Demo Trading

After the research model is created, ASTRA can run continuously in **OKX Demo Trading**.

### Demo rules

| Parameter | Current policy |
|---|---:|
| Real funds | **Disabled** |
| OKX mode | **Demo only** |
| Capital allocated to trading logic | **50% of Demo equity** |
| Capital reserve | **50%** |
| Max simultaneous positions | 8 |
| Risk per trade | 0.4% of allocated capital |
| Max position fraction | 10% of allocated capital |
| Minimum ML probability | 0.60 |
| Max holding time | 48 h |
| Retraining trigger | every 200 new lessons |

The Demo worker is designed to survive GitHub Actions runner restarts by checkpointing state, lessons and the current model back to the repository.

---

## 🛡️ Real-money gate

ASTRA **does not automatically switch to real trading**.

Before a real account can even be considered, the Demo track must satisfy the readiness gate implemented in `astra_bot/core/readiness.py`.

Current baseline criteria:

| Metric | Requirement |
|---|---:|
| Demo trading history | ≥ 30 trading days |
| Closed trades | ≥ 200 |
| Win rate | ≥ 55% |
| Profit factor | ≥ 1.3 |
| Max drawdown | ≤ 8% |
| Profitable days | ≥ 55% |
| Max loss streak | < 6 days |
| Sharpe | ≥ 1.0 |
| Readiness score | ≥ 85 / 100 |

**Important:** no software can guarantee that a real market position will never lose money. These criteria are a safety gate, not a profit guarantee.

The system sends a Telegram notification only when the readiness threshold is reached for the first time. It does not enable live trading by itself.

---

## 📲 Telegram

The intended operating model is deliberately quiet.

### Morning report — 09:00 MSK

The daily Telegram report is focused on the actual trading result:

```text
ASTRA BOT — morning report

Trades: 24
Wins: 15
Losses: 9
PnL: +38.42 USDT
```

The readiness system can additionally notify when the Demo history reaches the configured real-account gate.

---

## ☁️ Temporary GitHub-hosted operation

The current Demo environment is designed to run on **GitHub Actions** while the project does not yet have a paid VPS.

Because GitHub-hosted runners are not permanent machines, the worker uses a bounded-session architecture:

```text
runner starts
   ↓
Demo worker runs
   ↓
checkpoint state + lessons + model
   ↓
commit to master
   ↓
runner ends
   ↓
scheduled workflow starts again
   ↓
resume from checkpoint
```

Later the same Python trading worker can be moved to a VPS / Docker / systemd deployment without changing the core learning architecture.

---

## 🏗️ Repository structure

```text
astra_bot/
├── astra_bot/
│   ├── adapters/             # OKX and exchange adapters
│   ├── core/                 # state, risk, config, readiness, utilities
│   ├── engines/              # trading / risk engines
│   ├── ml/
│   │   ├── market_understanding.py   # chart → feature vector
│   │   ├── market_memory.py          # persistent pattern memory
│   │   ├── model_trainer.py          # model training / persistence
│   │   ├── weekly_learner.py         # continuous retraining
│   │   ├── self_play.py              # walk-forward simulation
│   │   └── news_features.py          # news context
│   └── strategies/           # strategy implementations
│
├── scripts/
│   ├── pretrain_exhaustive_5y.py     # historical research pass
│   ├── demo_trader_pro.py             # continuous Demo trader
│   ├── morning_report.py              # Telegram reporting
│   └── test_okx.py                    # safe private-API check
│
├── models/
│   ├── lessons.jsonl
│   ├── market_memory.json
│   ├── current.pkl
│   └── demo_state.json
│
├── .github/workflows/
│   ├── demo-trader.yml
│   ├── daily-train.yml
│   └── morning-report.yml
│
├── tests/
├── requirements.txt
└── README.md
```

---

## 🔐 Security rules

**Never commit secrets into the repository.**

OKX credentials and Telegram credentials belong in GitHub Secrets or a secure runtime environment.

Recommended OKX permissions:

```text
Read     ✅
Trade    ✅ only when real trading is intentionally enabled
Withdraw ❌ NEVER
```

The private API verification script checks connectivity/authentication without printing secret values.

---

## ⚙️ Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Safe OKX private-endpoint check
python scripts/test_okx.py

# Historical research
python scripts/pretrain_exhaustive_5y.py --years 5 --max-lessons 500000 --min-samples 2000 --with-news

# Demo trader
python scripts/demo_trader_pro.py
```

For Windows, use the equivalent `.venv\\Scripts\\activate` environment activation.

---

## 🧭 Project status

### Implemented

- [x] OKX integration
- [x] Demo trading mode
- [x] Risk engine
- [x] Walk-forward historical simulation
- [x] Structured lesson memory
- [x] Market-understanding feature engine
- [x] Pattern memory
- [x] ML model persistence
- [x] Continuous retraining
- [x] Five-year / maximum-history research path
- [x] 35-asset trading universe
- [x] Telegram morning report
- [x] Demo readiness gate
- [x] GitHub Actions temporary runtime

### In progress

- [ ] Long-running validation of the new market-aware model
- [ ] Continuous Demo performance analysis
- [ ] Temporal model validation / calibration improvements
- [ ] VPS deployment profile for permanent operation

### Not enabled

- [ ] Automatic real-money trading

---

## ⚠️ Disclaimer

ASTRA is an experimental software system for quantitative research and automated trading.

Historical performance, backtests, Demo results, ML metrics and readiness scores **do not guarantee future profitability**.

Real-money trading should remain disabled until the operator independently reviews the system, the risk limits, the Demo track record and the exchange configuration.

---

## 📄 License

MIT License

---

## 🔗 Documentation

- [OKX API Documentation](https://www.okx.com/docs-v5/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
