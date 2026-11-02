"""Best-effort Telegram trade charts; visualization only, no trading decisions."""
from __future__ import annotations

import io
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _value(candle: Any, key: str) -> float:
    raw = candle.get(key) if isinstance(candle, dict) else getattr(candle, key, None)
    if raw is None and isinstance(candle, dict):
        raw = candle.get({"open_time": "time"}.get(key, key))
    return float(raw or 0)


def render_trade_chart(
    candles_4h: list[Any],
    candles_1h: list[Any],
    *,
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    target: float | None,
    phase: str,
    exit_price: float | None = None,
) -> bytes:
    """Render last ~60 bars of 4h and 1h OHLCV with trade levels."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    series = (("4h", candles_4h[-60:]), ("1h", candles_1h[-60:]))
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharey=True)
    fig.patch.set_facecolor("#111318")
    for ax, (label, candles) in zip(axes, series):
        ax.set_facecolor("#171a21")
        if not candles:
            ax.text(0.5, 0.5, f"No {label} candles", transform=ax.transAxes,
                    ha="center", va="center", color="white")
        for i, candle in enumerate(candles):
            o, h, low, c = (_value(candle, k) for k in ("open", "high", "low", "close"))
            if not all(v > 0 for v in (o, h, low, c)):
                continue
            up = c >= o
            body_color = "#26a69a" if up else "#ef5350"
            ax.vlines(i, low, h, color=body_color, linewidth=1.0)
            bottom = min(o, c)
            height = max(abs(c - o), max(c * 0.00015, 1e-9))
            ax.add_patch(Rectangle((i - 0.32, bottom), 0.64, height,
                                   facecolor=body_color, edgecolor=body_color))
        n = max(len(candles), 1)
        left, right = -1, n
        low_level, high_level = sorted((entry, stop))
        if target is not None:
            low_level = min(low_level, target)
            high_level = max(high_level, target)
        span = max(high_level - low_level, entry * 0.01)
        pad = span * 0.35
        ax.set_xlim(left, right)
        ax.set_ylim(low_level - pad, high_level + pad)
        # Stop and profit zones are deliberately distinct for long/short.
        ax.axhspan(min(entry, stop), max(entry, stop), color="#8b0000", alpha=0.22)
        if target is not None:
            ax.axhspan(min(entry, target), max(entry, target),
                       color="#00e676", alpha=0.16)
        ax.axhline(entry, color="#f5f5f5", linewidth=1.2, linestyle="--")
        ax.text(1.005, entry, f" ENTRY {entry:g}", transform=ax.get_yaxis_transform(),
                color="white", va="center", fontsize=8)
        ax.axhline(stop, color="#b71c1c", linewidth=1.5)
        ax.text(1.005, stop, f" STOP {stop:g}", transform=ax.get_yaxis_transform(),
                color="#ff5252", va="center", fontsize=8)
        if target is not None:
            ax.axhline(target, color="#00e676", linewidth=1.5)
            ax.text(1.005, target, f" TARGET {target:g}",
                    transform=ax.get_yaxis_transform(), color="#69f0ae",
                    va="center", fontsize=8)
        if exit_price is not None and exit_price > 0:
            ax.axhline(exit_price, color="#ffd740", linewidth=1.2, linestyle=":")
            ax.text(1.005, exit_price, f" EXIT {exit_price:g}",
                    transform=ax.get_yaxis_transform(), color="#ffd740",
                    va="center", fontsize=8)
        ax.set_title(f"{label} • last {len(candles)} bars", color="white", loc="left")
        ax.grid(alpha=0.12)
        ax.tick_params(colors="#cfd8dc")
        for spine in ax.spines.values():
            spine.set_color("#424750")
    fig.suptitle(f"{symbol}  {side.upper()}  •  {phase}", color="white",
                 fontsize=14, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buffer.getvalue()


async def notify_trade_visual(
    client: Any,
    symbol: str,
    side: str,
    entry: Any,
    stop: Any,
    target: Any,
    *,
    phase: str,
    exit_price: Any = None,
) -> bool:
    """Fetch 4h/1h candles and send photo to the live clock's existing chat."""
    try:
        import aiohttp

        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = ((os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
                  or (os.environ.get("TELEGRAM_CHAT_ID") or "").strip())
        if not token or not chat_id:
            logger.warning("viz_error event=viz_error reason=telegram_config_missing")
            return False
        c4 = await client.get_candles(symbol, "4h", limit=60)
        c1 = await client.get_candles(symbol, "1h", limit=60)
        if not c4 or not c1:
            raise ValueError("empty_candles")
        png = await __import__("asyncio").to_thread(
            render_trade_chart, list(c4), list(c1), symbol=symbol, side=side,
            entry=float(entry), stop=float(stop),
            target=float(target) if target is not None else None,
            phase=phase,
            exit_price=float(exit_price) if exit_price is not None else None,
        )
        form = aiohttp.FormData()
        form.add_field("chat_id", chat_id)
        form.add_field("caption", f"{symbol} {side.upper()} • {phase}")
        form.add_field("photo", png, filename="trade.png", content_type="image/png")
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"telegram_http_{resp.status}")
                payload = await resp.json()
                if not payload.get("ok"):
                    raise RuntimeError("telegram_send_photo_not_ok")
        return True
    except Exception as exc:
        logger.warning("viz_error event=viz_error phase=%s symbol=%s error=%s",
                       phase, symbol, type(exc).__name__)
        return False
