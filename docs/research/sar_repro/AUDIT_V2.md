# SAR engine audit v2 (2026-11-18)

## Critical fixes applied

1. **Stop look-ahead removed**  
   Stop for bar `i` is `sar[i-1]` (state known at open of `i`).  
   Bar `i` OHLC only tests that precomputed stop. Gap through stop fills at open.

2. **Entry on next open**  
   Signal: trend flip on close of bar `i-1`.  
   Fill: open of bar `i` (+ slip).

3. **Portfolio PF from USDT**  
   `profit_factor_usd` = sum(winning pnl_usd) / abs(sum(losing pnl_usd)).  
   Legacy pct PF still reported as `profit_factor_pct_legacy` only for comparison.

## Invalidated prior results

| Claim (v1 buggy engine) | Status |
|-------------------------|--------|
| PF 1.70 | **Invalid** (look-ahead + close entry + pct PF) |
| Final ~200k / ~3M / millions at 1–3% risk | **Invalid** |

## v2 results (bot costs, ~1y 1h BTC+ETH+SOL, start 5000)

Snapshot after fixed engine:

- trades ~2060
- WR ~31%
- **PF_usd ~0.76–0.84 (below 1)**
- 1% risk: final ~111 (−98%), DD ~−98.5%
- Higher risk only accelerates path to near-zero

## Conclusion

Plain SAR 0.02/0.02/0.2 on 1h with bot costs and correct execution is **loss-making** on this window.  
Do **not** tune SAR parameters until any further simulator changes are audited.  
**DEMO unchanged.**

## Still open

- Funding = 0
- No frozen candle archive in git
- No walk-forward
- BingX pagination can return incomplete windows if rate-limited (re-run and check bar counts)
