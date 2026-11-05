"""Generate deterministic plus/minus EXIT card previews from canonical artwork."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location(
    "astra_trade_viz_preview", ROOT / "astra_bot" / "decision" / "trade_viz.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load trade card renderer")
TRADE_VIZ = module_from_spec(SPEC)
SPEC.loader.exec_module(TRADE_VIZ)
render_exit_stats_card = TRADE_VIZ.render_exit_stats_card
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
