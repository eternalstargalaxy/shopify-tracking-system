from __future__ import annotations

import time

from fastapi import HTTPException

from .config import settings
from .db import consume_rate_limit


def _window_start(window_seconds: int) -> int:
    now = int(time.time())
    return now - (now % window_seconds)


def enforce_ip_limits(client_ip: str) -> None:
    minute_count = consume_rate_limit("ip_minute", client_ip, _window_start(60))
    if minute_count > settings.ip_limit_per_minute:
        raise HTTPException(status_code=429, detail="Too many requests from this IP.")

    day_count = consume_rate_limit("ip_day", client_ip, _window_start(86400))
    if day_count > settings.ip_limit_per_day:
        raise HTTPException(status_code=429, detail="Daily request limit exceeded for this IP.")


def enforce_tracking_refresh_limit(tracking_key: str) -> None:
    refresh_count = consume_rate_limit(
        "tracking_refresh",
        tracking_key,
        _window_start(settings.tracking_refresh_window_seconds),
    )
    if refresh_count > settings.tracking_refresh_limit:
        raise HTTPException(
            status_code=429,
            detail="Tracking number refresh limit reached. Please retry later.",
        )
