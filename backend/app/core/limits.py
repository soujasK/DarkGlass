"""Hourly caps for the model-backed features on a public demo.

Each cap is a sliding one-hour window: one for the whole site and one per visitor. A cap of 0 means
no limit (the default, so a local run is never limited).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

WINDOW_S = 3600.0


class HourlyCap:
    def __init__(self, site_per_hour: int, visitor_per_hour: int) -> None:
        self.site_per_hour = site_per_hour
        self.visitor_per_hour = visitor_per_hour
        self._site: deque[float] = deque()
        self._visitors: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _trim(q: deque[float], now: float) -> None:
        while q and now - q[0] > WINDOW_S:
            q.popleft()

    def take(self, visitor: str) -> str | None:
        """Use one slot. Returns None when allowed, or which cap was hit ("site" or "visitor")."""
        now = time.time()
        with self._lock:
            self._trim(self._site, now)
            mine = self._visitors[visitor]
            self._trim(mine, now)
            if self.site_per_hour and len(self._site) >= self.site_per_hour:
                return "site"
            if self.visitor_per_hour and len(mine) >= self.visitor_per_hour:
                return "visitor"
            self._site.append(now)
            mine.append(now)
            return None


def visitor_of(request: Request) -> str:
    """The visitor's address. Behind Caddy the real one is the first X-Forwarded-For entry."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
