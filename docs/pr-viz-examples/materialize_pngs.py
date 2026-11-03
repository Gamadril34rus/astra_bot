"""Render the two owner-review PNG examples with the production EXIT renderer."""
from __future__ import annotations

from pathlib import Path

from astra_bot.decision.trade_viz import render_exit_stats_card

ROOT = Path(__file__).resolve().parent
ASSETS = Path(__file__).resolve().parents[2] / "astra_bot" / "decision" / "assets"


def main() -> None:
    qty_sol = 87.4861 * 100 * 6 / (94.679 * 1.50)
    qty_hype = 92.2984 * 100 * 6 / (86.713 * 1.10)
    closed_at = 1793652000.0
    cases = (
        ("sol-short-plus-1.50.png", "SOL-USDT", "short", 94.679, 94.443, 87.4861, qty_sol, 6, "art_plus.png"),
        ("hype-long-minus-1.10.png", "HYPE-USDT", "long", 86.713, 86.553, -92.2984, qty_hype, 6, "art_minus.png"),
    )
    for name, symbol, side, entry, exit_price, pnl, qty, lev, art in cases:
        png = render_exit_stats_card(
            symbol=symbol, side=side, entry=entry, exit_price=exit_price,
            pnl=pnl, qty=qty, leverage=lev, closed_at=closed_at,
            virtual=True, art_path=ASSETS / art,
        )
        path = ROOT / name
        path.write_bytes(png)
        print(f"wrote {path} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
