"""Generate deterministic plus/minus EXIT card previews from canonical artwork."""
from __future__ import annotations

from pathlib import Path

from astra_bot.decision.trade_viz import render_exit_stats_card

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "examples"
ASSETS = ROOT / "astra_bot" / "decision" / "assets"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = (
        {
            "filename": "trade_card_plus.png",
            "symbol": "SOL-USDT",
            "side": "short",
            "entry": 94.679,
            "exit_price": 94.443,
            "pnl": 87.4861,
            "qty": 87.4861 * 100 * 6 / (94.679 * 1.50),
            "leverage": 6,
            "art": "art_plus.png",
        },
        {
            "filename": "trade_card_minus.png",
            "symbol": "HYPE-USDT",
            "side": "long",
            "entry": 86.713,
            "exit_price": 86.553,
            "pnl": -92.2984,
            "qty": 92.2984 * 100 * 6 / (86.713 * 1.10),
            "leverage": 6,
            "art": "art_minus.png",
        },
    )
    for case in cases:
        filename = case.pop("filename")
        art = case.pop("art")
        png = render_exit_stats_card(
            **case,
            closed_at=1793652000,
            virtual=True,
            art_path=ASSETS / art,
        )
        target = OUT / filename
        target.write_bytes(png)
        print(f"wrote {target} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
