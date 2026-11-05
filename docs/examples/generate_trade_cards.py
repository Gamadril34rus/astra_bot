"""Generate deterministic plus/minus EXIT card previews from owner artwork."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER_PATH = ROOT / "astra_bot" / "decision" / "trade_viz.py"
SPEC = spec_from_file_location("astra_trade_viz_preview", RENDERER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load trade card renderer")
TRADE_VIZ = module_from_spec(SPEC)
SPEC.loader.exec_module(TRADE_VIZ)

OUT = ROOT / "docs" / "examples"


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
        },
    )
    for case in cases:
        filename = case.pop("filename")
        png = TRADE_VIZ.render_exit_stats_card(
            **case,
            closed_at=1793652000,
            virtual=True,
        )
        path = OUT / filename
        path.write_bytes(png)
        print(f"wrote {path} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
