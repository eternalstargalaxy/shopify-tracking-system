from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NormalizedStatus = Literal[
    "info_received",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "failed_attempt",
    "not_found",
    "expired",
    "unknown",
]

ErrorCode = Literal[
    "empty_tracking_number",
    "invalid_tracking_number",
    "invalid_shopify_signature",
    "expired_shopify_signature",
    "shop_not_allowed",
    "not_store_order",
    "rate_limited",
    "tracking_refresh_limited",
    "upstream_register_failed",
    "upstream_query_failed",
    "query_error",
]


class TrackingEvent(BaseModel):
    time: str = ""
    event_time: str = Field(default="", alias="eventTime")
    location: str = ""
    description: str = ""
    raw_status: str = ""
    provider_status: str = Field(default="", alias="providerStatus")
    provider_status_description: str = Field(default="", alias="providerStatusDescription")
    normalized_status: NormalizedStatus = Field(default="unknown", alias="normalizedStatus")

    model_config = {"populate_by_name": True}


class OrderSummaryItem(BaseModel):
    title: str
    quantity: int = 1
    variant: str | None = None
    image_url: str | None = Field(default=None, alias="imageUrl")
    item_url: str | None = Field(default=None, alias="itemUrl")
    unit_price: str | None = Field(default=None, alias="unitPrice")
    currency_code: str | None = Field(default=None, alias="currencyCode")

    model_config = {"populate_by_name": True}


class OrderSummary(BaseModel):
    order_name: str | None = Field(default=None, alias="orderName")
    placed_at: str | None = Field(default=None, alias="placedAt")
    fulfillment_status: str | None = Field(default=None, alias="fulfillmentStatus")
    financial_status: str | None = Field(default=None, alias="financialStatus")
    shipping_required: bool | None = Field(default=None, alias="shippingRequired")
    total_amount: str | None = Field(default=None, alias="totalAmount")
    currency_code: str | None = Field(default=None, alias="currencyCode")
    source: str | None = None
    items: list[OrderSummaryItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class TrackingShipment(BaseModel):
    tracking_number: str = Field(alias="trackingNumber")
    carrier_code: str | None = Field(default=None, alias="carrierCode")
    carrier_name: str | None = Field(default=None, alias="carrierName")
    last_mile_tracking_number: str | None = Field(default=None, alias="lastMileTrackingNumber")
    normalized_status: NormalizedStatus = Field(alias="normalizedStatus")
    status_text: str = Field(alias="statusText")
    provider_status: str | None = Field(default=None, alias="providerStatus")
    provider_status_description: str | None = Field(default=None, alias="providerStatusDescription")
    origin_country: str | None = Field(default=None, alias="originCountry")
    destination_country: str | None = Field(default=None, alias="destinationCountry")
    last_event_time: str | None = Field(default=None, alias="lastEventTime")
    updated_at: str = Field(alias="updatedAt")
    support_notice: str = Field(alias="supportNotice")
    cached: bool
    order_summary: OrderSummary | None = Field(default=None, alias="orderSummary")
    events: list[TrackingEvent] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class QueryError(BaseModel):
    tracking_number: str = Field(alias="trackingNumber")
    code: ErrorCode | str
    message: str

    model_config = {"populate_by_name": True}


class TrackResponse(BaseModel):
    success: bool = True
    ok: bool = True
    query_count: int = Field(alias="queryCount")
    shop_domain: str | None = Field(default=None, alias="shopDomain")
    generated_at: datetime = Field(alias="generatedAt")
    shipments: list[TrackingShipment]
    errors: list[QueryError]

    model_config = {"populate_by_name": True}


class InternalTrackRequest(BaseModel):
    nums: str
    carrier: str | None = None
    shop_domain: str | None = Field(default=None, alias="shopDomain")

    model_config = {"populate_by_name": True}


class InternalOrderTrackRequest(BaseModel):
    order_no: str = Field(alias="orderNo")
    email: str | None = None
    shop_domain: str | None = Field(default=None, alias="shopDomain")

    model_config = {"populate_by_name": True}


class RecentShipmentsResponse(BaseModel):
    count: int
    shipments: list[TrackingShipment]


class OpsEvent(BaseModel):
    event: str
    level: str
    message: str | None = None
    created_at: str = Field(alias="createdAt")
    context: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class OpsSummaryResponse(BaseModel):
    count_24h: int = Field(alias="count24h")
    error_count_24h: int = Field(alias="errorCount24h")
    warning_count_24h: int = Field(alias="warningCount24h")
    recent_events: list[OpsEvent] = Field(alias="recentEvents")

    model_config = {"populate_by_name": True}


class DailyUsageSummaryResponse(BaseModel):
    date: str
    first_seen_tracking_count: int = Field(alias="firstSeenTrackingCount")
    refreshed_tracking_count: int = Field(alias="refreshedTrackingCount")
    successful_query_count: int = Field(alias="successfulQueryCount")
    query_error_count: int = Field(alias="queryErrorCount")
    not_store_order_count: int = Field(alias="notStoreOrderCount")
    rate_limited_count: int = Field(alias="rateLimitedCount")
    notes: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
