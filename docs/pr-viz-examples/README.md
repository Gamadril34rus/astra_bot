# Exit stats card examples (PR #308)

The PNG previews are generated locally from the exact Pillow renderer used by the bot.

| Preview | Fixture |
|---|---|
| `sol-short-plus-1.50.png` | SOL short 94.679 → 94.443, +87.4861 USDT, 6X (+1.50%) |
| `hype-long-minus-1.10.png` | HYPE long 86.713 → 86.553, -92.2984 USDT, 6X (-1.10%) |

Run from the repository root:

```bash
python docs/pr-viz-examples/materialize_pngs.py
```

The script writes PNG previews into this directory. ENTRY chart rendering remains the existing `render_trade_chart` implementation; this preview workflow covers EXIT cards only.
