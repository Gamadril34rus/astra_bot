# ASTRA BOT by Gamadril — visual asset package

The original artwork, portrait crop, 1240×1640 card background, prebuilt
LONG/SHORT GIF animation, compact GIF, static fallback and SVG brand lockup
are included in the companion asset package.

Expected repository layout:

- astra_bot/decision/assets/gamadril/mascot_master.jpg
- astra_bot/decision/assets/gamadril/mascot_portrait.jpg
- astra_bot/decision/assets/gamadril/mascot_card_bg.jpg
- astra_bot/decision/assets/gamadril/long_short_animation.gif
- astra_bot/decision/assets/gamadril/mascot_animation_fallback.jpg
- docs/assets/astra_gamadril_lockup.svg

Do not remove the existing art_plus.png, art_minus.png, card_bg_plus.png or
card_bg_minus.png as part of the initial visual integration. The generated
artwork is an additive alternative until the owner reviews and approves the
new card composition.

Animation must be prepared in advance and cached. It must not be regenerated
on every five-minute trading tick. Missing or invalid animation assets must
fall back to the existing PNG card.

The source artwork contains no account metrics, fabricated PnL or trading
signals. The user-visible brand is ASTRA BOT by Gamadril; ZEUS remains an
internal project component.
