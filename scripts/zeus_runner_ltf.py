"""Zeus paper-clock split 5/9: 15m impulse telemetry (not an entry signal)."""

from __future__ import annotations

from zeus_runner_common import (
    BingXClient,
    LTF_IMPULSE_RANGE_MULT,
    LTF_IMPULSE_VOL_MULT,
    LTF_LOOKBACK,
    ZeusTradeLog,
    logger,
)

async def observe_ltf_impulse(
    *, bingx: BingXClient, journal: ZeusTradeLog, symbol: str
) -> None:
    try:
        k15 = await bingx.get_candles(symbol, "15m", limit=40)
        if k15 and len(k15) >= LTF_LOOKBACK:
            tail = k15[-LTF_LOOKBACK:]
            ranges = [float(c.high) - float(c.low) for c in tail]
            vols = [float(getattr(c, "volume", 0) or 0) for c in tail]
            avg_r = sum(ranges[:-1]) / max(len(ranges) - 1, 1)
            avg_v = sum(vols[:-1]) / max(len(vols) - 1, 1)
            last_r, last_v = ranges[-1], vols[-1]
            if avg_r > 0 and last_r >= avg_r * LTF_IMPULSE_RANGE_MULT:
                journal.ltf_impulse(
                    symbol=symbol,
                    timeframe="15m",
                    note="15m impulse; not entry alone",
                    range_mult=round(last_r / avg_r, 3) if avg_r else None,
                    vol_mult=round(last_v / avg_v, 3) if avg_v else None,
                )
    except Exception as ltf_exc:
        logger.debug("ltf: %s", ltf_exc)
