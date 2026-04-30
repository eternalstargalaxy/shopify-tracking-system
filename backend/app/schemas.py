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


class TrackingShipment(BaseModel):
    tracking_number: str = Field(alias="trackingNumber")
    carrier_code: str | None = Field(default=None, alias="carrierCode")
    carrier_name: str | None = Field(default=None, alias="carrierName")
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
    events: list[TrackingEvent] = []

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
