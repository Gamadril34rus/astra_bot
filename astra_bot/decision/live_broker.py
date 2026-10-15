"""LiveBroker: exchange is truth; hard cap $100; risk % of real equity.

Does not touch PaperBroker. HALT/kill checked before any place_order.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# TEMP: full restore pending — import from master via next commit
raise ImportError("live_broker PLACEHOLDER was pushed; restoring in next commit")
