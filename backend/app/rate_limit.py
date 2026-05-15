from __future__ import annotations

import time

from fastapi import HTTPException

from .config import settings
from .db import consume_rate_limit
from .observability import log_event, monitor_event_spike


def _window_start(window_seconds: int) -> int:
    now = int(time.time())
    return now - (now % window_seconds)


def enforce_ip_limits(client_ip: str) -> None:
    minute_count = consume_rate_limit("ip_minute", client_ip, _window_start(60))
    if minute_count > settings.ip_limit_per_minute:
        log_event(
            "ip_rate_limited",
            level="warning",
            message="Per-minute IP limit exceeded.",
            client_ip=client_ip,
            minute_count=minute_count,
            minute_limit=settings.ip_limit_per_minute,
        )
        monitor_event_spike(
            source_events=("ip_rate_limited", "ip_daily_rate_limited"),
            alert_event="rate_limit_spike",
            threshold=settings.alert_rate_limit_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Rate-limit rejections spiked within the alert window.",
            client_ip=client_ip,
        )
        raise HTTPException(status_code=429, detail="Too many requests from this IP.")

    day_count = consume_rate_limit("ip_day", client_ip, _window_start(86400))
    if day_count > settings.ip_limit_per_day:
        log_event(
            "ip_daily_rate_limited",
            level="warning",
            message="Per-day IP limit exceeded.",
            client_ip=client_ip,
            day_count=day_count,
            day_limit=settings.ip_limit_per_day,
        )
        monitor_event_spike(
            source_events=("ip_rate_limited", "ip_daily_rate_limited"),
            alert_event="rate_limit_spike",
            threshold=settings.alert_rate_limit_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Rate-limit rejections spiked within the alert window.",
            client_ip=client_ip,
        )
        raise HTTPException(status_code=429, detail="Daily request limit exceeded for this IP.")


def enforce_tracking_refresh_limit(tracking_key: str) -> None:
    refresh_count = consume_rate_limit(
        "tracking_refresh",
        tracking_key,
        _window_start(settings.tracking_refresh_window_seconds),
    )
    if refresh_count > settings.tracking_refresh_limit:
        log_event(
            "tracking_refresh_limited",
            level="warning",
            message="Tracking refresh limit exceeded.",
            tracking_key=tracking_key,
            refresh_count=refresh_count,
            refresh_limit=settings.tracking_refresh_limit,
            refresh_window_seconds=settings.tracking_refresh_window_seconds,
        )
        raise HTTPException(
            status_code=429,
            detail="Tracking number refresh limit reached. Please retry later.",
        )
