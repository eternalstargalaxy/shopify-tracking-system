from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import HTTPException, Request

from .config import settings
from .db import replace_order_tracking_numbers_for_order_name
from .observability import log_event
from .shopify_admin import ShopifyAdminClient, extract_tracking_references

SUPPORTED_WEBHOOK_TOPICS = {
    "orders/create",
    "orders/updated",
    "fulfillments/create",
    "fulfillments/update",
}
shopify_admin_client = ShopifyAdminClient()


def verify_webhook_request(request: Request, raw_body: bytes) -> tuple[str, str]:
    if not settings.shopify_app_secret:
        log_event(
            "webhook_config_missing_secret",
            level="error",
            message="SHOPIFY_APP_SECRET is missing while webhook verification is enabled.",
            path=request.url.path,
        )
        raise HTTPException(status_code=500, detail="Missing SHOPIFY_APP_SECRET configuration.")

    signature = request.headers.get("X-Shopify-Hmac-Sha256", "").strip()
    if not signature:
        log_event(
            "webhook_signature_missing",
            level="warning",
            message="Missing Shopify webhook signature.",
            path=request.url.path,
        )
        raise HTTPException(status_code=401, detail="Missing Shopify webhook signature.")

    computed = base64.b64encode(
        hmac.new(
            settings.shopify_app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    if not hmac.compare_digest(computed, signature):
        log_event(
            "webhook_signature_invalid",
            level="warning",
            message="Invalid Shopify webhook signature.",
            path=request.url.path,
        )
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature.")

    shop_domain = request.headers.get("X-Shopify-Shop-Domain", "").strip().lower()
    if not shop_domain:
        log_event(
            "webhook_shop_missing",
            level="warning",
            message="Missing Shopify webhook shop domain.",
            path=request.url.path,
        )
        raise HTTPException(status_code=400, detail="Missing Shopify webhook shop domain.")

    if settings.allowed_shop_domains and shop_domain not in settings.allowed_shop_domains:
        log_event(
            "webhook_shop_not_allowed",
            level="warning",
            message="Webhook shop domain is not allowed.",
            path=request.url.path,
            shop_domain=shop_domain,
            allowed_shop_domains=settings.allowed_shop_domains,
        )
        raise HTTPException(status_code=403, detail="Shop domain is not allowed.")

    topic = request.headers.get("X-Shopify-Topic", "").strip().lower()
    return shop_domain, topic


def sync_tracking_mappings_from_webhook(
    topic: str,
    shop_domain: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if topic not in SUPPORTED_WEBHOOK_TOPICS:
        log_event(
            "webhook_topic_ignored",
            message="Ignoring unsupported Shopify webhook topic.",
            topic=topic,
            shop_domain=shop_domain,
        )
        return {"topic": topic, "shopDomain": shop_domain, "syncedCount": 0, "orderName": None}

    order_payload = _resolve_order_payload(topic, shop_domain, payload)
    if not order_payload:
        log_event(
            "webhook_order_payload_missing",
            level="warning",
            message="Shopify webhook did not provide an order payload that could be resolved.",
            topic=topic,
            shop_domain=shop_domain,
        )
        return {"topic": topic, "shopDomain": shop_domain, "syncedCount": 0, "orderName": None}

    order_name = str(order_payload.get("name") or "").strip()
    tracking_numbers = [
        reference.tracking_number
        for reference in extract_tracking_references(order_payload)
    ]
    synced_count = replace_order_tracking_numbers_for_order_name(
        shop_domain,
        order_name,
        tracking_numbers,
        source="shopify_webhook",
    )
    log_event(
        "shopify_webhook_synced",
        message="Synchronized Shopify fulfillment tracking mappings from webhook payload.",
        topic=topic,
        shop_domain=shop_domain,
        order_name=order_name,
        synced_count=synced_count,
        tracking_numbers=tracking_numbers,
    )
    return {
        "topic": topic,
        "shopDomain": shop_domain,
        "syncedCount": synced_count,
        "orderName": order_name or None,
    }


def _resolve_order_payload(topic: str, shop_domain: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if topic.startswith("orders/"):
        return payload

    if topic.startswith("fulfillments/"):
        order_id = payload.get("order_id")
        if order_id:
            return shopify_admin_client.fetch_order_payload(shop_domain, order_id)
        if payload.get("name") and payload.get("fulfillments"):
            return payload
    return None


def parse_webhook_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        return json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Shopify webhook payload.") from exc
