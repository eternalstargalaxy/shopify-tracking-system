from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings
from .db import consume_rate_limit, insert_system_event


def _window_start(window_seconds: int) -> int:
    now = int(time.time())
    return now - (now % window_seconds)


def log_event(event: str, *, level: str = "info", message: str | None = None, **fields: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "level": level,
        "message": message,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=True), flush=True)
    insert_system_event(event, level, message, fields)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_feishu_webhook(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname.endswith("feishu.cn") or hostname.endswith("larksuite.com")


def _sign_feishu(timestamp: str, secret: str) -> str:
    payload = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(payload, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_alert_payload(event: str, level: str, message: str, fields: dict[str, Any]) -> dict[str, Any]:
    timestamp = str(int(time.time()))
    if _is_feishu_webhook(settings.alert_webhook_url):
        lines = [
            f"[tracking-alert] [{settings.app_name}] [{level.upper()}] {event}",
            f"Time: {_utc_now_text()}",
            f"Message: {message}",
        ]
        if fields:
            lines.append("Context:")
            for key, value in fields.items():
                lines.append(f"- {key}: {value}")
        payload = {
            "msg_type": "text",
            "content": {"text": "\n".join(lines)},
        }
        if settings.alert_webhook_secret:
            payload["timestamp"] = timestamp
            payload["sign"] = _sign_feishu(timestamp, settings.alert_webhook_secret)
        return payload

    return {
        "text": f"[tracking-alert] {event}: {message}",
        "event": event,
        "level": level,
        "message": message,
        "context": fields,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def send_alert(event: str, message: str, *, level: str = "error", **fields: Any) -> None:
    if not settings.alert_webhook_url:
        return

    count = consume_rate_limit(
        "alert",
        event,
        _window_start(max(settings.alert_min_interval_seconds, 60)),
    )
    if count > 1:
        return

    payload = _build_alert_payload(event, level, message, fields)
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
        log_event("alert_delivery_failed", level="error", message="Alert webhook delivery failed.", alert_event=event)
