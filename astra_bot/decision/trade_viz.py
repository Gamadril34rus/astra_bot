"""Best-effort Telegram trade charts; visualization only, no trading decisions."""
from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timedelta, timezone
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
    for ax, (label, candles) in zip(axes, series, strict=True):
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
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=25)) as resp,
        ):
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


# EXIT share card: isolated from the existing ENTRY chart renderer.
_MSK = timezone(timedelta(hours=3))


def leveraged_roi_pct(*, entry: float, pnl: float, qty: float, leverage: float) -> float:
    """BingX-style leveraged ROI, using realized PnL already reconciled at close."""
    if entry <= 0 or qty <= 0:
        return 0.0
    return pnl / (entry * qty) * 100.0 * leverage


def render_exit_stats_card(
    *, symbol: str, side: str, entry: float, exit_price: float, pnl: float,
    qty: float, leverage: float, closed_at: float | None = None,
    virtual: bool = True, art_path: str | os.PathLike[str] | None = None,
) -> bytes:
    """Render a Pillow PNG exit card; optional artwork never blocks fallback rendering."""
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageEnhance, ImageFont

    roi = leveraged_roi_pct(entry=entry, pnl=pnl, qty=qty, leverage=leverage)
    positive = pnl >= 0
    is_long = str(side).lower() == "long"
    side_label = "Long" if is_long else "Short"
    side_color = "#00C853" if is_long else "#B71C1C"
    result_color = "#00E676" if positive else "#FF5252"
    symbol_label = str(symbol).replace("-", "").replace("/", "")
    if not symbol_label.endswith("USDT"):
        symbol_label += "USDT"
    stamp = datetime.fromtimestamp(
        closed_at if closed_at is not None else datetime.now(_MSK).timestamp(), _MSK
    )
    stamp_text = stamp.strftime("%d.%m.%Y  %H:%M:%S МСК")

    width, height = 1240, 1640
    image = Image.new("RGB", (width, height), "#0B0D12")
    draw = ImageDraw.Draw(image)
    panel = (48, 48, width - 48, height - 48)
    draw.rounded_rectangle(panel, radius=44, fill="#141820")

    # Draw artwork first. Text is deliberately rendered afterwards.
    if art_path is None:
        art_path = Path(__file__).parent / "assets" / ("art_plus.png" if positive else "art_minus.png")
    try:
        with Image.open(art_path) as source:
            source = source.convert("RGBA")
            w, h = source.size
            crop = source.crop((int(0.58*w), int(0.01*h), w, int(0.71*h)))
            if crop.width > 0 and crop.height > 0:
                max_h = int(height * 0.70)
                ratio = min(max_h / crop.height, (width * 0.60) / crop.width, 1.0)
                size = (max(1, int(crop.width * ratio)), max(1, int(crop.height * ratio)))
                crop = crop.resize(size, Image.Resampling.LANCZOS)
                alpha = crop.getchannel("A")
                rgb = Image.new("RGB", crop.size, "#000000")
                rgb.paste(crop.convert("RGB"), mask=alpha)
                rgb = ImageEnhance.Brightness(rgb).enhance(0.45)
                x = width - 76 - rgb.width
                y = height - 170 - rgb.height
                image.paste(rgb, (x, y))
                draw = ImageDraw.Draw(image)
    except (OSError, ValueError, TypeError):
        pass

    def font(size: int, bold: bool = False):
        candidates = (
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
            if bold else
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
        )
        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()

    # Header
    draw.text((92, 92), "BingX", fill="#FFFFFF", font=font(42, True))
    draw.text((92, 230), symbol_label, fill="#FFFFFF", font=font(60, True))
    draw.text((92, 320), "Perpetual", fill="#9CA3AF", font=font(30))
    draw.text((92, 405), side_label, fill=side_color, font=font(34, True))
    draw.text((300, 405), f"|  {leverage:g}X  |  Closed", fill="#D1D5DB", font=font(30))

    draw.text((92, 560), f"{roi:+.2f}%", fill=result_color, font=font(104, True))
    draw.text((92, 690), f"{pnl:+.4f} USDT", fill=result_color, font=font(48, True))
    if virtual:
        draw.text((92, 755), "(виртуальные)", fill="#6B7280", font=font(26))

    draw.text((92, 930), "Цена входа", fill="#FFFFFF", font=font(30))
    draw.text((520, 920), f"{entry:.6g}", fill="#FFFFFF", font=font(38, True))
    draw.text((92, 1035), "Цена закрытия", fill="#FFFFFF", font=font(30))
    draw.text((520, 1025), f"{exit_price:.6g}", fill="#FFFFFF", font=font(38, True))
    draw.text((92, height - 125), stamp_text, fill="#9CA3AF", font=font(24))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(200, 200), optimize=True)
    return buffer.getvalue()


async def notify_exit_stats_card(
    *, symbol: str, side: str, entry: float, exit_price: float, pnl: float,
    qty: float, leverage: float, closed_at: float | None = None,
    virtual: bool = True,
) -> bool:
    """Send a rendered EXIT card; never performs exchange reads."""
    try:
        import aiohttp
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = ((os.environ.get("TELEGRAM_ADMIN_ID") or "").strip()
                   or (os.environ.get("TELEGRAM_CHAT_ID") or "").strip())
        if not token or not chat_id:
            logger.warning("viz_error event=viz_error reason=telegram_config_missing")
            return False
        png = await __import__("asyncio").to_thread(
            render_exit_stats_card, symbol=symbol, side=side, entry=entry,
            exit_price=exit_price, pnl=pnl, qty=qty, leverage=leverage,
            closed_at=closed_at, virtual=virtual,
        )
        form = aiohttp.FormData()
        form.add_field("chat_id", chat_id)
        form.add_field("caption", f"{symbol} {side.upper()} • EXIT")
        form.add_field("photo", png, filename="exit_card.png", content_type="image/png")
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=form, timeout=aiohttp.ClientTimeout(total=25),
            ) as response,
        ):
            if response.status >= 400:
                raise RuntimeError(f"telegram_http_{response.status}")
            payload = await response.json()
            if not payload.get("ok"):
                raise RuntimeError("telegram_send_photo_not_ok")
        return True
    except Exception as exc:
        logger.warning("viz_error event=viz_error phase=EXIT_CARD symbol=%s error=%s",
                       symbol, type(exc).__name__)
        return False
