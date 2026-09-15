# tsm45 hygiene — binance vision / .com mirror

**Verdict unchanged:** panel OOS PF 0.85 stands. No 35-pair shadow; no OOS-symbol subset.

## Source note

- `api.binance.com` returns **HTTP 451** in research sandbox.
- Hygiene re-check used **`data-api.binance.vision`** (official Binance data API mirror of .com spot klines).

| Symbol | Vision API | Note |
|--------|------------|------|
| BTCUSDT | OK — OOS PF ~1.71 mean +0.019 (n=16) | majors still can look fine in isolation |
| ETHUSDT | OK — OOS PF ~3.6 n=14 | small n |
| TONUSDT | OK — OOS PF ~1.30 mean +0.015 n=13 | does not flip panel |
| XMRUSDT | OK history, **0 trades** under frozen band/lookback | — |
| CROUSDT | HTTP 400 on this endpoint | still missing |

Isolated major-pair OOS positives **do not** override the 32-pair aggregate (selection bias if used to green-light).

**Confirmed:** hygiene pass on vision/.com-mirror for available symbols; **panel rejection of general tsm45 remains**.
