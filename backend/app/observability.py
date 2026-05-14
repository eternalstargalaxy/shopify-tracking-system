from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings
from .db import consume_rate_limit


def _window_start(window_seconds: int) -> int:
    now = int(time.time())
    return now - (now % window_seconds)


def log_event(event: str, **fields: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def send_alert(event: str, message: str, **fields: Any) -> None:
    if not settings.alert_webhook_url:
        return

    count = consume_rate_limit(
        "alert",
        event,
        _window_start(max(settings.alert_min_interval_seconds, 60)),
    )
    if count > 1:
        return

    payload = {
        "text": f"[tracking-alert] {event}: {message}",
        "event": event,
        "message": message,
        "context": fields,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    request = Request(
        url=settings.alert_webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError):
        log_event("alert_delivery_failed", alert_event=event)
