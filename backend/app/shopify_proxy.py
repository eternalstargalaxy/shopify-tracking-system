from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from .config import settings
from .observability import log_event, monitor_event_spike


def _build_message(query_string: str) -> tuple[str, str | None]:
    params = parse_qsl(query_string, keep_blank_values=True)
    grouped: dict[str, list[str]] = defaultdict(list)
    signature = None
    for key, value in params:
        if key == "signature":
            signature = value
            continue
        grouped[key].append(value)

    parts: list[str] = []
    for key in sorted(grouped):
        joined_value = ",".join(grouped[key])
        parts.append(f"{key}={joined_value}")
    return "".join(parts), signature


def verify_proxy_request(request: Request) -> str | None:
    if settings.bypass_proxy_signature:
        return request.query_params.get("shop")

    proxy_failure_events = (
        "proxy_signature_missing",
        "proxy_signature_invalid",
        "proxy_timestamp_invalid",
        "proxy_timestamp_expired",
        "proxy_shop_not_allowed",
    )

    if not settings.shopify_app_secret:
        log_event(
            "proxy_config_missing_secret",
            level="error",
            message="SHOPIFY_APP_SECRET is missing while proxy signature verification is enabled.",
            path=request.url.path,
        )
        raise HTTPException(status_code=500, detail="Missing SHOPIFY_APP_SECRET configuration.")

    raw_query = request.url.query
    message, signature = _build_message(raw_query)
    if not signature:
        log_event(
            "proxy_signature_missing",
            level="warning",
            message="Missing Shopify app proxy signature.",
            path=request.url.path,
            query=raw_query,
        )
        monitor_event_spike(
            source_events=proxy_failure_events,
            alert_event="proxy_rejection_spike",
            threshold=settings.alert_proxy_failure_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Shopify proxy verification failures spiked within the alert window.",
            path=request.url.path,
        )
        raise HTTPException(status_code=401, detail="Missing Shopify app proxy signature.")

    computed = hmac.new(
        settings.shopify_app_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, signature):
        log_event(
            "proxy_signature_invalid",
            level="warning",
            message="Invalid Shopify app proxy signature.",
            path=request.url.path,
            query=raw_query,
        )
        monitor_event_spike(
            source_events=proxy_failure_events,
            alert_event="proxy_rejection_spike",
            threshold=settings.alert_proxy_failure_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Shopify proxy verification failures spiked within the alert window.",
            path=request.url.path,
        )
        raise HTTPException(status_code=401, detail="Invalid Shopify app proxy signature.")

    timestamp = request.query_params.get("timestamp")
    if timestamp and settings.hmac_max_age_seconds > 0:
        try:
            age = abs(int(time.time()) - int(timestamp))
        except ValueError as exc:
            log_event(
                "proxy_timestamp_invalid",
                level="warning",
                message="Invalid Shopify proxy timestamp.",
                path=request.url.path,
                query=raw_query,
                timestamp=timestamp,
            )
            monitor_event_spike(
                source_events=proxy_failure_events,
                alert_event="proxy_rejection_spike",
                threshold=settings.alert_proxy_failure_spike_threshold,
                window_seconds=settings.alert_spike_window_seconds,
                message="Shopify proxy verification failures spiked within the alert window.",
                path=request.url.path,
            )
            raise HTTPException(status_code=401, detail="Invalid Shopify proxy timestamp.") from exc
        if age > settings.hmac_max_age_seconds:
            log_event(
                "proxy_timestamp_expired",
                level="warning",
                message="Expired Shopify proxy timestamp.",
                path=request.url.path,
                query=raw_query,
                timestamp=timestamp,
                max_age_seconds=settings.hmac_max_age_seconds,
            )
            monitor_event_spike(
                source_events=proxy_failure_events,
                alert_event="proxy_rejection_spike",
                threshold=settings.alert_proxy_failure_spike_threshold,
                window_seconds=settings.alert_spike_window_seconds,
                message="Shopify proxy verification failures spiked within the alert window.",
                path=request.url.path,
            )
            raise HTTPException(status_code=401, detail="Expired Shopify proxy timestamp.")

    shop_domain = request.query_params.get("shop")
    if settings.allowed_shop_domains and shop_domain not in settings.allowed_shop_domains:
        log_event(
            "proxy_shop_not_allowed",
            level="warning",
            message="Shop domain is not allowed.",
            path=request.url.path,
            shop_domain=shop_domain,
            allowed_shop_domains=settings.allowed_shop_domains,
        )
        monitor_event_spike(
            source_events=proxy_failure_events,
            alert_event="proxy_rejection_spike",
            threshold=settings.alert_proxy_failure_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Shopify proxy verification failures spiked within the alert window.",
            path=request.url.path,
            shop_domain=shop_domain,
        )
        raise HTTPException(status_code=403, detail="Shop domain is not allowed.")

    return shop_domain
