from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_MSK = timezone(timedelta(hours=3))


def render_gamadril_exit_animation(
    *,
    symbol: str,
    side: str,
    entry: float,
    exit_price: float,
    pnl: float,
    qty: float,
    leverage: float,
    closed_at: float | None = None,
    virtual: bool = True,
    animation_path: str | os.PathLike[str] | None = None,
) -> bytes:
    """Overlay exit metrics onto a prebuilt GIF; effects are never generated per tick."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

    path = Path(animation_path) if animation_path is not None else (
        Path(__file__).parent / "assets" / "gamadril" / "long_short_animation.gif"
    )
    if not path.is_file():
        raise FileNotFoundError("Gamadril animation asset is missing")

    if entry <= 0 or qty <= 0:
        raise ValueError("entry_and_qty_must_be_positive")
    normalized = str(side).lower()
    if normalized not in {"long", "short"}:
        raise ValueError("side_must_be_long_or_short")
    roi = pnl / (entry * qty) * 100.0 * leverage
    is_long = normalized == "long"
    side_color = (35, 201, 130, 255) if is_long else (237, 91, 98, 255)
    pnl_color = (35, 201, 130, 255) if pnl >= 0 else (237, 91, 98, 255)
    timestamp = datetime.fromtimestamp(
        closed_at if closed_at is not None else datetime.now(_MSK).timestamp(), _MSK
    ).strftime("%d.%m.%Y %H:%M:%S MSK")

    def font(size: int, bold: bool = False):
        names = (
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
            if bold else
            ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
        )
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
        return ImageFont.load_default()

    frames: list[Image.Image] = []
    durations: list[int] = []
    with Image.open(path) as source:
        for item in ImageSequence.Iterator(source):
            frame = ImageOps.fit(
                item.convert("RGBA"), (960, 1280), method=Image.Resampling.LANCZOS
            )
            draw = ImageDraw.Draw(frame, "RGBA")
            draw.rounded_rectangle(
                (44, 830, 916, 1110), radius=28,
                fill=(5, 8, 13, 202), outline=(214, 180, 106, 235), width=3,
            )
            draw.text(
                (76, 858), f"{symbol}  {normalized.upper()}  {leverage:g}X",
                fill=side_color, font=font(34, True),
            )
            draw.text(
                (76, 918), f"{pnl:+.4f} USDT  |  {roi:+.2f}%",
                fill=pnl_color, font=font(42, True),
            )
            draw.text(
                (76, 982), f"ENTRY {entry:.6g}    EXIT {exit_price:.6g}",
                fill=(245, 247, 250, 255), font=font(27, True),
            )
            prefix = "(virtual)  |  " if virtual else ""
            draw.text(
                (76, 1042), f"{prefix}{timestamp}",
                fill=(190, 198, 208, 255), font=font(21),
            )
            frames.append(frame.convert("RGB"))
            durations.append(int(source.info.get("duration", 180)))

    if not frames:
        raise ValueError("Gamadril animation contains no frames")
    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=0, optimize=True, disposal=2,
    )
    return output.getvalue()
