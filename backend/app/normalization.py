from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import settings

STATUS_LABELS = {
    "info_received": "Info received",
    "in_transit": "In transit",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "exception": "Exception",
    "failed_attempt": "Delivery attempt failed",
    "not_found": "No tracking updates",
    "expired": "Tracking expired",
    "unknown": "Status unknown",
}

MAIN_STATUS_MAP = {
    "InfoReceived": "info_received",
    "InTransit": "in_transit",
    "OutForDelivery": "out_for_delivery",
    "AvailableForPickup": "out_for_delivery",
    "Delivered": "delivered",
    "DeliveryFailure": "failed_attempt",
    "Exception": "exception",
    "NotFound": "not_found",
    "Expired": "expired",
}

KEYWORD_FALLBACKS = {
    "delivered": "delivered",
    "out for delivery": "out_for_delivery",
    "delivery attempted": "failed_attempt",
    "failed attempt": "failed_attempt",
    "exception": "exception",
    "customs": "in_transit",
    "transit": "in_transit",
    "picked up": "in_transit",
    "label created": "info_received",
    "information received": "info_received",
    "not found": "not_found",
    "expired": "expired",
}


def normalize_status(main_status: str | None, sub_status: str | None, status_text: str | None) -> str:
    if main_status in MAIN_STATUS_MAP:
        return MAIN_STATUS_MAP[main_status]

    search_text = " ".join(part for part in [main_status, sub_status, status_text] if part).lower()
    for keyword, normalized in KEYWORD_FALLBACKS.items():
        if keyword in search_text:
            return normalized
    return "unknown"


def status_label(normalized_status: str) -> str:
    return STATUS_LABELS.get(normalized_status, STATUS_LABELS["unknown"])


def support_notice(normalized_status: str) -> str:
    messages = {
        "info_received": "The carrier has not scanned the parcel yet.",
        "in_transit": "The shipment is moving through the carrier network.",
        "out_for_delivery": "The parcel is in the final-mile delivery stage.",
        "delivered": "Delivery has been completed.",
        "exception": "A shipment exception needs attention.",
        "failed_attempt": "A delivery attempt was made but did not complete.",
        "not_found": "No tracking updates are available yet.",
        "expired": "Tracking is no longer being actively updated.",
        "unknown": "The shipment status could not be classified yet.",
    }
    return messages[normalized_status]


def compute_cache_expiry(normalized_status: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if normalized_status == "delivered":
        return now + timedelta(hours=settings.cache_ttl_delivered_hours)
    if normalized_status in {"not_found", "unknown"}:
        return now + timedelta(hours=settings.cache_ttl_not_found_hours)
    if normalized_status in {"exception", "failed_attempt"}:
        return now + timedelta(minutes=settings.cache_ttl_exception_minutes)
    if normalized_status == "expired":
        return now + timedelta(hours=24)
    return now + timedelta(minutes=settings.cache_ttl_active_minutes)
