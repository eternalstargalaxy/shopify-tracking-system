from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .config import settings
from .db import (
    fetch_order_tracking_match,
    fetch_tracking_record,
    is_store_order_tracking_number,
    list_recent_tracking_records,
    upsert_order_tracking_number,
    upsert_tracking_record,
)
from .normalization import compute_cache_expiry, status_label, support_notice
from .observability import log_event, monitor_event_spike, send_alert
from .rate_limit import enforce_tracking_refresh_limit
from .schemas import QueryError, TrackingShipment
from .seventeen_track import SeventeenTrackClient, parse_track_info
from .seventeen_track_storefront import SeventeenTrackStorefrontClient, StoreOrderLookup
from .shopify_admin import ShopifyAdminClient, build_local_order_summary, merge_order_summaries

TRACKING_PATTERN = re.compile(r"[A-Za-z0-9]{6,42}")
ORDER_NUMBER_PATTERN = re.compile(r"^(?:[A-Z]{2,6}\d{3,8}|#\d{3,8})$")
shopify_admin_client = ShopifyAdminClient()
storefront_client = SeventeenTrackStorefrontClient()


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


def normalize_order_number(raw_value: str) -> str:
    return (raw_value or "").strip().upper()


def is_valid_order_number(raw_value: str) -> bool:
    return bool(ORDER_NUMBER_PATTERN.match(normalize_order_number(raw_value)))


def record_is_fresh(record: dict | None, now: datetime | None = None) -> bool:
    if not record or not record["cache_expires_at"]:
        return False
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(record["cache_expires_at"]) > now


def _record_has_tracking_events(record: dict | None) -> bool:
    if not record:
        return False
    try:
        return bool(json.loads(record["events_json"] or "[]"))
    except json.JSONDecodeError:
        return False


def build_cached_shipment(record) -> TrackingShipment:
    events = json.loads(record["events_json"] or "[]")
    return TrackingShipment(
        trackingNumber=record["tracking_number"],
        carrierCode=record["carrier_code"],
        carrierName=record["carrier_name"],
        lastMileTrackingNumber=None,
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


def resolve_order_summary(
    tracking_number: str,
    carrier_code: str | None,
    shop_domain: str | None,
    storefront_lookup: StoreOrderLookup | None = None,
) -> object | None:
    match = fetch_order_tracking_match(tracking_number, carrier_code, shop_domain)
    local_summary = build_local_order_summary(match)
    storefront_summary = storefront_lookup.order_summary if storefront_lookup else None
    order_name = (
        (match["order_name"] if match else None)
        or (storefront_lookup.order_name if storefront_lookup else None)
    )
    admin_summary = None
    if order_name and shop_domain:
        admin_summary = shopify_admin_client.lookup_order_summary(shop_domain, order_name)

    summary = merge_order_summaries(admin_summary, storefront_summary)
    return merge_order_summaries(summary, local_summary)


def resolve_order_summary_for_lookup(
    storefront_lookup: StoreOrderLookup,
    shop_domain: str | None,
) -> object | None:
    local_summary = build_local_order_summary(
        {
            "order_name": storefront_lookup.order_name,
            "source": "manual",
        }
    )
    admin_summary = None
    if storefront_lookup.order_name and shop_domain:
        admin_summary = shopify_admin_client.lookup_order_summary(
            shop_domain,
            storefront_lookup.order_name,
        )

    summary = merge_order_summaries(admin_summary, storefront_lookup.order_summary)
    return merge_order_summaries(summary, local_summary)


def build_unshipped_order_shipment(
    storefront_lookup: StoreOrderLookup,
    shop_domain: str | None,
) -> TrackingShipment:
    now = datetime.now(timezone.utc).isoformat()
    return TrackingShipment(
        trackingNumber="",
        carrierCode=storefront_lookup.carrier_code,
        carrierName=storefront_lookup.carrier_name,
        lastMileTrackingNumber=storefront_lookup.last_mile_tracking_number,
        normalizedStatus="unknown",
        statusText="Not shipped yet",
        providerStatus="Not shipped",
        providerStatusDescription="This order has not been shipped yet.",
        originCountry=None,
        destinationCountry=storefront_lookup.destination_country,
        lastEventTime=None,
        updatedAt=now,
        supportNotice="This order has not been shipped yet.",
        cached=False,
        orderSummary=resolve_order_summary_for_lookup(storefront_lookup, shop_domain),
        events=[],
    )


def build_unshipped_admin_order_shipment(order_summary: object) -> TrackingShipment:
    now = datetime.now(timezone.utc).isoformat()
    return TrackingShipment(
        trackingNumber="",
        carrierCode=None,
        carrierName=None,
        lastMileTrackingNumber=None,
        normalizedStatus="unknown",
        statusText="Not shipped yet",
        providerStatus="Not shipped",
        providerStatusDescription="This order has not been shipped yet.",
        originCountry=None,
        destinationCountry=None,
        lastEventTime=None,
        updatedAt=now,
        supportNotice="This order has not been shipped yet.",
        cached=False,
        orderSummary=order_summary,
        events=[],
    )


def query_tracking_numbers(
    client: SeventeenTrackClient,
    tracking_numbers: list[str],
    carrier_code: str | None,
    shop_domain: str | None,
    *,
    enforce_order_match: bool | None = None,
) -> tuple[list[TrackingShipment], list[QueryError]]:
    shipments: list[TrackingShipment] = []
    errors: list[QueryError] = []
    for tracking_number in tracking_numbers:
        try:
            shipment, error = process_tracking_number(
                client,
                tracking_number,
                carrier_code,
                shop_domain,
                enforce_order_match=enforce_order_match,
            )
            if shipment:
                shipments.append(shipment)
            if error:
                errors.append(error)
        except Exception as exc:
            message = str(getattr(exc, "detail", exc)) or "Unknown query error."
            log_event(
                "tracking_query_error",
                tracking_number=tracking_number,
                carrier_code=carrier_code,
                shop_domain=shop_domain,
                error=message,
            )
            errors.append(
                QueryError(
                    trackingNumber=tracking_number,
                    code="query_error",
                    message=message,
                )
            )
    return shipments, errors


def get_recent_shipments(limit: int = 20) -> list[TrackingShipment]:
    rows = list_recent_tracking_records(limit)
    return [build_cached_shipment(row) for row in rows]


def _result_score(parsed: dict) -> tuple[int, int, int]:
    return (
        len(parsed.get("events") or []),
        1 if parsed.get("last_event_time") else 0,
        0 if parsed.get("normalized_status") in {"not_found", "unknown"} else 1,
    )


def _should_retry_with_detected_carrier(parsed: dict, carrier_code: str | None) -> bool:
    detected_carrier = parsed.get("carrier_code")
    if not detected_carrier or detected_carrier == carrier_code:
        return False
    return parsed.get("normalized_status") in {"not_found", "unknown"} and not parsed.get("events")


def _should_refresh_empty_record(
    record: dict | None,
    storefront_lookup: StoreOrderLookup | None,
) -> bool:
    if not record or not storefront_lookup:
        return False
    if record["normalized_status"] not in {"unknown", "not_found"}:
        return False
    if _record_has_tracking_events(record):
        return False
    return bool(storefront_lookup.order_name or storefront_lookup.tracking_params.get("num"))


def process_tracking_number(
    client: SeventeenTrackClient,
    tracking_number: str,
    carrier_code: str | None,
    shop_domain: str | None,
    *,
    enforce_order_match: bool | None = None,
    storefront_lookup_override: StoreOrderLookup | None = None,
) -> tuple[TrackingShipment | None, QueryError | None]:
    record = fetch_tracking_record(tracking_number, carrier_code)
    storefront_lookup = storefront_lookup_override or storefront_client.lookup_by_tracking(tracking_number, shop_domain)
    resolved_carrier_code = (
        carrier_code
        or (storefront_lookup.carrier_code if storefront_lookup else None)
        or (record["carrier_code"] if record else None)
        or None
    )
    should_enforce_order_match = (
        settings.require_order_tracking_match
        if enforce_order_match is None
        else enforce_order_match
    )
    store_match = is_store_order_tracking_number(
        tracking_number,
        carrier_code,
        shop_domain,
    )
    if storefront_lookup and storefront_lookup.order_name and shop_domain:
        upsert_tracking_order_mapping(
            tracking_number,
            storefront_lookup,
            resolved_carrier_code,
            shop_domain,
        )
        store_match = True

    if storefront_lookup and not storefront_lookup.tracking_params.get("fc") and storefront_lookup.order_name:
        log_event(
            "storefront_lookup_missing_tracking_params",
            level="warning",
            tracking_number=tracking_number,
            carrier_code=carrier_code,
            shop_domain=shop_domain,
            order_name=storefront_lookup.order_name,
        )

    if _should_refresh_empty_record(record, storefront_lookup):
        record = None

    if should_enforce_order_match and not store_match:
        log_event(
            "tracking_not_store_order",
            tracking_number=tracking_number,
            carrier_code=carrier_code,
            shop_domain=shop_domain,
        )
        monitor_event_spike(
            source_events=("tracking_not_store_order", "order_lookup_not_found"),
            alert_event="not_store_order_spike",
            threshold=settings.alert_not_store_order_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Store-order validation rejections spiked within the alert window.",
            shop_domain=shop_domain,
        )
        return None, QueryError(
            trackingNumber=tracking_number,
            code="not_store_order",
            message="This tracking number does not belong to an order in this store.",
        )

    if record_is_fresh(record):
        shipment = build_cached_shipment(record)
        shipment.last_mile_tracking_number = (
            getattr(storefront_lookup, "last_mile_tracking_number", None) if storefront_lookup else None
        )
        shipment.order_summary = resolve_order_summary(
            tracking_number,
            resolved_carrier_code,
            shop_domain,
            storefront_lookup,
        )
        return shipment, None

    enforce_tracking_refresh_limit(f"{tracking_number}:{resolved_carrier_code or 'auto'}")

    is_registered = bool(record["is_registered"]) if record else False
    if not is_registered:
        client.register(tracking_number, resolved_carrier_code)

    parsed = None
    if storefront_lookup and storefront_lookup.tracking_params.get("fc"):
        detail_response = storefront_client.fetch_tracking_detail(
            storefront_lookup,
            tracking_number,
            shop_domain,
        )
        if detail_response:
            parsed = parse_track_info(detail_response, tracking_number)

    if not parsed or (
        parsed.get("normalized_status") in {"unknown", "not_found"} and not parsed.get("events")
    ):
        raw_response = client.get_track_info(tracking_number, resolved_carrier_code)
        parsed = parse_track_info(raw_response, tracking_number)
        if _should_retry_with_detected_carrier(parsed, resolved_carrier_code):
            detected_carrier = parsed["carrier_code"]
            client.register(tracking_number, detected_carrier)
            retry_response = client.get_track_info(tracking_number, detected_carrier)
            retry_parsed = parse_track_info(retry_response, tracking_number)
            if _result_score(retry_parsed) > _result_score(parsed):
                parsed = retry_parsed

    if parsed.get("normalized_status") in {"unknown", "not_found"} and not parsed.get("events"):
        log_event(
            "tracking_no_updates",
            tracking_number=tracking_number,
            carrier_code=parsed.get("carrier_code") or resolved_carrier_code,
            shop_domain=shop_domain,
            source="storefront" if storefront_lookup else "public_api",
        )

    if storefront_lookup:
        if not parsed.get("carrier_code") and storefront_lookup.carrier_code:
            parsed["carrier_code"] = storefront_lookup.carrier_code
        if not parsed.get("carrier_name") and storefront_lookup.carrier_name:
            parsed["carrier_name"] = storefront_lookup.carrier_name
        if not parsed.get("destination_country") and storefront_lookup.destination_country:
            parsed["destination_country"] = storefront_lookup.destination_country

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
        lastMileTrackingNumber=(
            getattr(storefront_lookup, "last_mile_tracking_number", None) if storefront_lookup else None
        ),
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
        orderSummary=resolve_order_summary(
            parsed["tracking_number"],
            parsed["carrier_code"],
            shop_domain,
            storefront_lookup,
        ),
        events=parsed["events"],
    )
    log_event(
        "tracking_query_success",
        tracking_number=shipment.tracking_number,
        carrier_code=shipment.carrier_code,
        normalized_status=shipment.normalized_status,
        cached=shipment.cached,
        event_count=len(shipment.events),
        order_name=shipment.order_summary.order_name if shipment.order_summary else None,
    )
    return shipment, None


def query_order_tracking(
    client: SeventeenTrackClient,
    order_number: str,
    email: str,
    shop_domain: str | None,
) -> tuple[list[TrackingShipment], list[QueryError]]:
    normalized_order_number = normalize_order_number(order_number)
    if not is_valid_order_number(normalized_order_number):
        return [], [
            QueryError(
                trackingNumber=normalized_order_number or order_number,
                code="invalid_tracking_number",
                message="That order number format is not valid. Please check it and try again.",
            )
        ]
    admin_lookup = shopify_admin_client.lookup_order_by_name_and_email(
        shop_domain,
        normalized_order_number,
        email,
    )
    if admin_lookup:
        for tracking_ref in admin_lookup.tracking_numbers:
            upsert_order_tracking_number(
                tracking_number=tracking_ref.tracking_number,
                carrier_code="",
                shop_domain=shop_domain,
                order_name=admin_lookup.order_summary.order_name,
                source="shopify_admin",
            )

        if admin_lookup.shipment_pending:
            shipment = build_unshipped_admin_order_shipment(admin_lookup.order_summary)
            log_event(
                "order_lookup_pending_shipment",
                order_number=normalized_order_number,
                email=email,
                shop_domain=shop_domain,
                order_name=admin_lookup.order_summary.order_name,
                source="shopify_admin",
            )
            return [shipment], []

        if admin_lookup.tracking_numbers:
            primary_tracking = admin_lookup.tracking_numbers[0]
            shipment, error = process_tracking_number(
                client,
                primary_tracking.tracking_number,
                None,
                shop_domain,
                enforce_order_match=True,
            )
            shipments = [shipment] if shipment else []
            errors = [error] if error else []
            if errors:
                send_alert(
                    "order_lookup_failed",
                    f"Order lookup failed for {normalized_order_number}",
                    order_number=normalized_order_number,
                    email=email,
                    shop_domain=shop_domain,
                    errors=[item.message for item in errors],
                    source="shopify_admin",
                )
            return shipments, errors

    lookup = storefront_client.lookup_by_order(normalized_order_number, email, shop_domain)
    tracking_number = (lookup.tracking_params.get("num") if lookup else None) or None
    if lookup and lookup.shipment_pending:
        shipment = build_unshipped_order_shipment(lookup, shop_domain)
        log_event(
            "order_lookup_pending_shipment",
            order_number=normalized_order_number,
            email=email,
            shop_domain=shop_domain,
            order_name=lookup.order_name,
            source="17track_shopify",
        )
        return [shipment], []

    if not lookup or not tracking_number:
        log_event(
            "order_lookup_not_found",
            order_number=normalized_order_number,
            email=email,
            shop_domain=shop_domain,
            admin_checked=bool(admin_lookup),
        )
        monitor_event_spike(
            source_events=("tracking_not_store_order", "order_lookup_not_found"),
            alert_event="not_store_order_spike",
            threshold=settings.alert_not_store_order_spike_threshold,
            window_seconds=settings.alert_spike_window_seconds,
            message="Store-order validation rejections spiked within the alert window.",
            shop_domain=shop_domain,
        )
        return [], [
            QueryError(
                trackingNumber=normalized_order_number or order_number,
                code="not_store_order",
                message="We couldn't find an order matching that order number and email address.",
            )
        ]

    shipment, error = process_tracking_number(
        client,
        tracking_number,
        lookup.carrier_code,
        shop_domain,
        enforce_order_match=False,
        storefront_lookup_override=lookup,
    )
    shipments = [shipment] if shipment else []
    errors = [error] if error else []
    if errors:
        send_alert(
            "order_lookup_failed",
            f"Order lookup failed for {normalized_order_number}",
            order_number=normalized_order_number,
            email=email,
            shop_domain=shop_domain,
            errors=[item.message for item in errors],
            source="17track_shopify",
        )
    return shipments, errors


def upsert_tracking_order_mapping(
    tracking_number: str,
    storefront_lookup: StoreOrderLookup,
    carrier_code: str | None,
    shop_domain: str,
) -> None:
    upsert_order_tracking_number(
        tracking_number=tracking_number,
        carrier_code=carrier_code or storefront_lookup.carrier_code,
        shop_domain=shop_domain,
        order_name=storefront_lookup.order_name,
        source="17track_shopify",
    )
