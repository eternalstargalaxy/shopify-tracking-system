from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .config import settings
from .db import fetch_tracking_record, is_store_order_tracking_number, upsert_tracking_record
from .normalization import compute_cache_expiry, status_label, support_notice
from .rate_limit import enforce_tracking_refresh_limit
from .schemas import QueryError, TrackingShipment
from .seventeen_track import SeventeenTrackClient, parse_track_info

TRACKING_PATTERN = re.compile(r"[A-Za-z0-9]{6,42}")


def parse_tracking_numbers(raw_value: str) -> list[str]:
    numbers = TRACKING_PATTERN.findall(raw_value or "")
    deduped = []
    seen = set()
    for number in numbers:
        upper = number.upper()
        if any(char.isdigit() for char in upper) and upper not in seen:
            seen.add(upper)
            deduped.append(upper)
    return deduped[:40]


def record_is_fresh(record: dict | None, now: datetime | None = None) -> bool:
    if not record or not record["cache_expires_at"]:
        return False
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(record["cache_expires_at"]) > now


def build_cached_shipment(record) -> TrackingShipment:
    events = json.loads(record["events_json"] or "[]")
    return TrackingShipment(
        trackingNumber=record["tracking_number"],
        carrierCode=record["carrier_code"],
        carrierName=record["carrier_name"],
        normalizedStatus=record["normalized_status"],
        statusText=record["status_text"] or status_label(record["normalized_status"]),
        providerStatus=record["provider_status"],
        providerStatusDescription=record["provider_status_description"],
        originCountry=record["origin_country"],
        destinationCountry=record["destination_country"],
        lastEventTime=record["last_event_time"],
        updatedAt=record["last_fetched_at"] or "",
        supportNotice=support_notice(record["normalized_status"]),
        cached=True,
        events=events,
    )


def process_tracking_number(
    client: SeventeenTrackClient,
    tracking_number: str,
    carrier_code: str | None,
    shop_domain: str | None,
) -> tuple[TrackingShipment | None, QueryError | None]:
    record = fetch_tracking_record(tracking_number, carrier_code)
    if settings.require_order_tracking_match and not is_store_order_tracking_number(
        tracking_number,
        carrier_code,
        shop_domain,
    ):
        return None, QueryError(
            trackingNumber=tracking_number,
            code="not_store_order",
            message="This tracking number does not belong to an order in this store.",
        )

    if record_is_fresh(record):
        return build_cached_shipment(record), None

    enforce_tracking_refresh_limit(f"{tracking_number}:{carrier_code or 'auto'}")

    is_registered = bool(record["is_registered"]) if record else False
    if not is_registered:
        client.register(tracking_number, carrier_code)

    raw_response = client.get_track_info(tracking_number, carrier_code)
    parsed = parse_track_info(raw_response, tracking_number)
    now = datetime.now(timezone.utc)
    cache_expires_at = compute_cache_expiry(parsed["normalized_status"], now)

    payload = {
        **parsed,
        "shop_domain": shop_domain,
        "is_registered": True,
        "last_fetched_at": now.isoformat(),
        "cache_expires_at": cache_expires_at.isoformat(),
        "last_error_code": None,
        "last_error_message": None,
    }
    upsert_tracking_record(payload)

    shipment = TrackingShipment(
        trackingNumber=parsed["tracking_number"],
        carrierCode=parsed["carrier_code"],
        carrierName=parsed["carrier_name"],
        normalizedStatus=parsed["normalized_status"],
        statusText=parsed["status_text"] or status_label(parsed["normalized_status"]),
        providerStatus=parsed["provider_status"],
        providerStatusDescription=parsed["provider_status_description"],
        originCountry=parsed["origin_country"],
        destinationCountry=parsed["destination_country"],
        lastEventTime=parsed["last_event_time"],
        updatedAt=payload["last_fetched_at"],
        supportNotice=support_notice(parsed["normalized_status"]),
        cached=False,
        events=parsed["events"],
    )
    return shipment, None
