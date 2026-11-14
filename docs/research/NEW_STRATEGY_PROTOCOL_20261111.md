# New strategy research protocol — 2026-11-11

**Verdict: NOT PASSED.**  
No hypothesis satisfied the full gate set (IS → Validation → OOS → cost stress → multi-symbol independence → ≥100 independent OOS trades).

Live Zeus, SETTINGS, risk, HALT, REAL — **unchanged**.

## 1. Data contract

| Source | Value |
|--------|--------|
| Branch | `research/positioning-data` |
| Path | `data/bingx_usdm/*_30m.csv` |
| Symbols | BTC, ETH, SOL, HYPE, INJ |
| Gaps / duplicates | 0 / 0 on audited files |
| `taker_buy_volume` | **Empty on all rows** — not used (not treated as zero) |
| `trades_count` | All zeros — not used |
| OI archive | Prospective snapshots only — **not** used as history |
| Funding | Used where present (`funding_*.csv`) |

### SHA-256 of 30m archives (download used for this report)

| File | SHA-256 |
|------|---------|
| BTCUSDT_30m.csv | `4b2fc0c969f48b900f92ead78e5a6245334a7bc96306a02e392c828ab9a31cae` |
| ETHUSDT_30m.csv | `d1c720873bf46e679d61123c6ab0ff993119aa9136b58446f3c61437abab1f75` |
| SOLUSDT_30m.csv | `a814dcf07490e88def3668e07cc4905436f57b3c3d659065642797a2e3496875` |
| HYPEUSDT_30m.csv | `e41e8776139fc0564b8357aadc3d62d610221029f673a4661bb6cf3c1724e22d` |
| INJUSDT_30m.csv | `fd2345df610bac3b27f86c9b628e4db594b596a5f0adc0992e551d06e5fde52b` |

Coverage (approx.): SOL from 2025-04, BTC from 2025-07, ETH from 2025-08, INJ from 2025-09, HYPE from 2026-04 → through 2026-11-11.

## 2. Execution model

- Signal on **closed** 30m bar `i`; entry at **open** of `i+1`
- Stop / take intrabar (stop priority)
- Max hold **18 bars (9h)**; min conceptual horizon 30m
- Fee **4 bps** + slip **2 bps** per side; stress = ×1.5
- Funding ≈ rate × (hold_hours / 8) with sign by side
- Risk **1%** equity per trade via stop distance
- Chronological splits per symbol: **IS 50% / Val 25% / OOS 25%**
- No shuffle; no parameter fit on OOS

## 3. Hypothesis journal (all kept)

### Wave 1 — structure / vol / session

| ID | Idea | Why it might work | IS+Val gate |
|----|------|-------------------|-------------|
| A | Failed range expansion reversion (wick+CLV) | Liquidity sweep / rejection after expansion | Fail (IS net < 0) |
| B | ATR compression → break + volume | Regime shift continuation | Fail (IS net < 0; Val occasionally +) |
| C | Momentum exhaustion (3 bars + RV spike) | Crowded impulse fade | Fail |
| D | Session range fade/continue by UTC hour | Thin vs active liquidity | Fail |
| E | Funding z × elevated RV | Crowding only when vol forces de-risk | Fail |
| F | Inside-bar failed break | False breakout | Fail |

### Wave 2 — residual / path

| ID | Idea | Why it might work | IS+Val gate |
|----|------|-------------------|-------------|
| resid_cont | Alt residual continuation with vol | Underreaction to idiosyncratic flow | Fail on Val |
| body_persist | Strong body + EMA slope | Short-horizon persistence | Fail |
| expand_pause | Expand then pause, trend-aligned | Continuation after digestion | Fail on Val |
| thin_quiet_fade | Quiet thin-hours extreme fade | Mean reversion in low participation | Fail |
| active_pullback | Active hours pullback in trend | Classic pullback, session-gated | Fail |
| **resid_revert** | 6-bar residual vs BTC mean-revert + reclaim | Idiosyncratic overshoot mean-reverts | **Passed IS+Val** |

### Prior art avoided / noted

- Pure EMA/RSI/MACD grids and channel breakouts (see `scripts/research_free_strategies.py`)
- Funding-only extreme fade without RV/residual structure (prior research branch)
- Taker-imbalance rules (**data empty**)
- Historical OI rules (**not available**)

## 4. Near-miss: residual mean-reversion (`resid_revert`)

**Rule (frozen after IS exploration):**

1. `resid = ret_alt - ret_BTC` on same 30m bar  
2. `resid_sum6 = sum(resid, 6)`  
3. Long if `resid_sum6 < -3%` and close location value > 0.55 and ATR not chaotic (`atr/atr_ma < 2`)  
4. Short if `resid_sum6 > +3%` and CLV < 0.45 and same ATR cap  
5. Stop 2.0×ATR, take 2.5R, max hold 18 bars  

### Pooled results (ETH+SOL+HYPE+INJ; BTC excluded as residual base)

| Split | N | Net (10k model) | Notes |
|-------|---:|----------------:|-------|
| IS | ~129 | **+1629** | 3 symbols positive |
| Validation | 64 | **+525** | med PF ~1.37 |
| OOS | 62 | **+268** | stress ×1.5 still **+130** |

### Why it still **fails** the TZ gates

| Gate | Required | Observed |
|------|----------|----------|
| OOS PF > 1.20 independent | Yes | Pool weak; **INJ dominates** (~56/62 OOS trades) |
| Not one-coin dependent | Yes | **Fail** — ETH OOS ~0 trades, SOL ~2, HYPE ~4 |
| ≥100 OOS trades | Target | **62** — insufficient |
| Multi-symbol OOS positive | ≥2 with n≥5 | **Only 1** |

This is a **research lead**, not a passed strategy.

## 5. Engineering delivered

| Artifact | Path |
|----------|------|
| Strategy module (shadow-ready interface) | `astra_bot/strategies/research/residual_revert.py` |
| Reproducible backtest CLI | `scripts/research/run_strategy_protocol.py` |
| Unit tests (no look-ahead helpers) | `tests/research/test_residual_revert_signals.py` |
| This report | `docs/research/NEW_STRATEGY_PROTOCOL_20261111.md` |

Config is research-only YAML: `config/research/residual_revert.yaml`.

## 6. How to reproduce

```bash
git fetch origin research/positioning-data
mkdir -p data/bingx_usdm
git checkout origin/research/positioning-data -- data/bingx_usdm

pip install pandas numpy  # if needed
python scripts/research/run_strategy_protocol.py --data-dir data/bingx_usdm
pytest tests/research/test_residual_revert_signals.py -q
```

## 7. Risks and limits

- BingX history depth differs by symbol → pooled calendar is uneven.
- Residual uses BTC as factor; regime changes in BTC beta can break the rule.
- Empty taker fields block aggressor-side hypotheses until backfill marker completes.
- OI cannot support historical tests until long prospective collection exists.
- Positive OOS on INJ alone is **not** portfolio evidence.

## 8. Explicit verdict

**Стратегия проверку не прошла.**  
Нет воспроизводимого статистического преимущества на multi-symbol OOS после издержек по критериям ТЗ.

Ближайший кандидат: **residual mean-reversion vs BTC** — оставить в journal, копить данные, не подключать к live/paper arm без нового OOS с ≥2 символами и ≥100 сделками.
