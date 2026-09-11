"""Token-bucket / sliding-window limiter for Telegram sends (TZ P2.6)."""

from __future__ import annotations

import time
from collections import deque


class TelegramRateLimiter:
    """Allow at most ``max_per_minute`` sends, with a short burst cap.

    Critical alerts (HALT / error) bypass the soft cap but still cannot
    exceed ``critical_per_minute`` (default 5).
    """

    def __init__(
        self,
        max_per_minute: int = 20,
        burst: int = 5,
        critical_per_minute: int = 5,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.burst = burst
        self.critical_per_minute = critical_per_minute
        self._times: deque[float] = deque()
        self._critical_times: deque[float] = deque()
        self._burst_window: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._times and now - self._times[0] >= 60.0:
            self._times.popleft()
        while self._critical_times and now - self._critical_times[0] >= 60.0:
            self._critical_times.popleft()
        while self._burst_window and now - self._burst_window[0] >= 2.0:
            self._burst_window.popleft()

    def allow(self, *, critical: bool = False) -> bool:
        now = time.monotonic()
        self._prune(now)
        if critical:
            if len(self._critical_times) >= self.critical_per_minute:
                return False
            self._critical_times.append(now)
            self._times.append(now)
            return True
        if len(self._burst_window) >= self.burst:
            return False
        if len(self._times) >= self.max_per_minute:
            return False
        self._burst_window.append(now)
        self._times.append(now)
        return True
