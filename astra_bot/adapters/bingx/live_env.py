"""Live env helpers (no network deps). VST default; mainnet only ZEUS_MAINNET=1."""
from __future__ import annotations

import os

BINGX_API_BASE = "https://open-api.bingx.com"
BINGX_VST_BASE = "https://open-api-vst.bingx.com"


def _redact(s: object) -> str:
    t = str(s or "")
    if len(t) > 8:
        return t[:3] + "***" + t[-2:]
    return "***"


def resolve_base_url() -> str:
    mainnet = (os.environ.get("ZEUS_MAINNET") or "").strip() == "1"
    vst = (os.environ.get("ZEUS_VST") or "1").strip()
    if mainnet and vst not in ("1", "true", "yes"):
        return BINGX_API_BASE
    return BINGX_VST_BASE
