from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from .config import settings


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

    if not settings.shopify_app_secret:
        raise HTTPException(status_code=500, detail="Missing SHOPIFY_APP_SECRET configuration.")

    raw_query = request.url.query
    message, signature = _build_message(raw_query)
    if not signature:
        raise HTTPException(status_code=401, detail="Missing Shopify app proxy signature.")

    computed = hmac.new(
        settings.shopify_app_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=401, detail="Invalid Shopify app proxy signature.")

    timestamp = request.query_params.get("timestamp")
    if timestamp and settings.hmac_max_age_seconds > 0:
        try:
            age = abs(int(time.time()) - int(timestamp))
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid Shopify proxy timestamp.") from exc
        if age > settings.hmac_max_age_seconds:
            raise HTTPException(status_code=401, detail="Expired Shopify proxy timestamp.")

    shop_domain = request.query_params.get("shop")
    if settings.allowed_shop_domains and shop_domain not in settings.allowed_shop_domains:
        raise HTTPException(status_code=403, detail="Shop domain is not allowed.")

    return shop_domain
