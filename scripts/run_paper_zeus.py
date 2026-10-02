#!/usr/bin/env python3
"""Zeus paper-clock entrypoint. Body split into zeus_runner_* (MCP payload limit)."""

import sys

from zeus_runner_impl import main

if __name__ == "__main__":
    sys.exit(main())
