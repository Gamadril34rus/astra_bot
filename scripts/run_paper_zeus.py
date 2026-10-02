#!/usr/bin/env python3
"""Zeus paper-clock: entrypoint. Тело в zeus_runner_impl (сплит из-за лимита payload MCP)."""
import sys
from pathlib import Path

# Ensure scripts/ on path when invoked as python scripts/run_paper_zeus.py
_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from zeus_runner_impl import main

if __name__ == "__main__":
    sys.exit(main())
