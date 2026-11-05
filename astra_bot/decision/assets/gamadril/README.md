# ASTRA BOT by Gamadril — visual assets

The companion asset archive contains the original master, portrait crop,
card background, prebuilt LONG/SHORT animation variants, static fallback
and SVG lockup.

Runtime asset path:

    astra_bot/decision/assets/gamadril/long_short_animation.gif

The renderer only reads this prebuilt GIF when an EXIT notification is
created. It overlays realized trade values and reuses
`leveraged_roi_pct()` from the existing visualization module.

The Telegram sender tries `sendAnimation`. If the animation file is absent,
invalid, or Telegram rejects the animation, it renders and sends the existing
PNG EXIT card through `sendPhoto`.

The GIF is not generated or recompressed on a five-minute loop. Existing
`art_plus.png`, `art_minus.png`, `card_bg_plus.png` and
`card_bg_minus.png` remain unchanged. The new visuals are additive and
limited to EXIT presentation.
