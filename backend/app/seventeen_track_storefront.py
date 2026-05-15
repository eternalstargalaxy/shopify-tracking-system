from __future__ import annotations

import json
from dataclasses import dataclass
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import settings
from .observability import log_event, send_alert
from .schemas import OrderSummary, OrderSummaryItem


@dataclass
class StoreOrderLookup:
    order_name: str | None
    carrier_code: str | None
    carrier_name: str | None
    destination_country: str | None
    last_mile_tracking_number: str | None
    order_summary: OrderSummary | None
    tracking_params: dict
    raw_response: dict


class SeventeenTrackStorefrontClient:
    def __init__(self) -> None:
        self.base_url = settings.seventeen_track_shopify_url
        self.tracking_url = settings.seventeen_track_shopify_tracking_url

    def lookup_by_tracking(self, tracking_number: str, shop_domain: str | None) -> StoreOrderLookup | None:
        shop_slug = _resolve_shop_slug(shop_domain)
        if not shop_slug:
            return None

        payload = {
            "Version": "1.0",
            "Client": "shopify",
            "Method": "get-track-record-by-track-no",
            "SourceType": "0",
            "Cookies": "",
            "TimeZoneOffset": 0,
            "Param": {
                "shop": shop_slug,
                "track_no": tracking_number,
            },
        }
        raw = self._post(payload, shop_domain, tracking_number=tracking_number)
        return _parse_store_lookup(raw)

    def lookup_by_order(
        self,
        order_number: str,
        email: str,
        shop_domain: str | None,
    ) -> StoreOrderLookup | None:
        shop_slug = _resolve_shop_slug(shop_domain)
        if not shop_slug:
            return None

        payload = {
            "Version": "1.0",
            "Client": "shopify",
            "Method": "get-track-record-by-order-no",
            "SourceType": "0",
            "Cookies": "",
            "TimeZoneOffset": 0,
            "Param": {
                "shop": shop_slug,
                "order_no": order_number,
                "user_email": email,
            },
        }
        raw = self._post(payload, shop_domain, order_number=order_number)
        return _parse_store_lookup(raw)

    def fetch_tracking_detail(
        self,
        lookup: StoreOrderLookup,
        tracking_number: str,
        shop_domain: str | None,
        *,
        language: str = "en",
    ) -> dict:
        tracking_params = lookup.tracking_params or {}
        if not tracking_params.get("fc") or not tracking_params.get("g"):
            return {}

        payload = {
            "data": [
                {
                    "num": tracking_params.get("num") or tracking_number,
                    "fc": tracking_params.get("fc"),
                    "sc": tracking_params.get("sc") or 0,
                    "params": tracking_params.get("params") or {},
                }
            ],
            "g": tracking_params.get("g"),
            "lang": language,
            "timeZoneOffset": 0,
        }

        guid = None
        for _ in range(3):
            if guid:
                payload["guid"] = guid
            raw = self._post_tracking(payload, shop_domain, tracking_number=tracking_number)
            shipments = raw.get("shipments") or []
            shipment = shipments[0] if shipments else {}
            if shipment.get("code") == 200 and shipment.get("shipment"):
                return raw
            guid = raw.get("guid") or guid
            if shipment.get("code") != 100:
                return raw
            sleep(1.6)
        return raw

    def _post(
        self,
        payload: dict,
        shop_domain: str | None,
        *,
        tracking_number: str | None = None,
        order_number: str | None = None,
    ) -> dict:
        storefront_url = _resolve_storefront_url(shop_domain)
        if not storefront_url:
            return {}

        if tracking_number:
            query = urlencode({"nums": tracking_number})
            referer = f"{storefront_url}/apps/17TRACK?{query}"
        elif order_number:
            referer = f"{storefront_url}/apps/17TRACK"
        else:
            referer = f"{storefront_url}/apps/17TRACK"

        request = Request(
            url=self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": storefront_url,
                "Referer": referer,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log_event(
                "storefront_lookup_failed",
                tracking_number=tracking_number,
                order_number=order_number,
                shop_domain=shop_domain,
                error_type=type(exc).__name__,
            )
            send_alert(
                "storefront_lookup_failed",
                "17TRACK storefront summary lookup failed.",
                tracking_number=tracking_number,
                order_number=order_number,
                shop_domain=shop_domain,
                error_type=type(exc).__name__,
            )
            return {}

    def _post_tracking(
        self,
        payload: dict,
        shop_domain: str | None,
        *,
        tracking_number: str | None = None,
    ) -> dict:
        storefront_url = _resolve_storefront_url(shop_domain)
        if not storefront_url:
            return {}

        query = urlencode({"nums": tracking_number}) if tracking_number else ""
        referer = f"{storefront_url}/apps/17TRACK"
        if query:
            referer = f"{referer}?{query}"

        request = Request(
            url=self.tracking_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": storefront_url,
                "Referer": referer,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            log_event(
                "storefront_tracking_failed",
                tracking_number=tracking_number,
                shop_domain=shop_domain,
                error_type=type(exc).__name__,
            )
            send_alert(
                "storefront_tracking_failed",
                "17TRACK storefront tracking detail lookup failed.",
                tracking_number=tracking_number,
                shop_domain=shop_domain,
                error_type=type(exc).__name__,
            )
            return {}


def _resolve_shop_slug(shop_domain: str | None) -> str | None:
    domain = (shop_domain or "").strip()
    if domain:
        return domain.split(".", 1)[0]

    explicit = (settings.shopify_store_slug or "").strip()
    if explicit:
        return explicit

    if settings.allowed_shop_domains:
        return settings.allowed_shop_domains[0].split(".", 1)[0]
    return None


def _resolve_storefront_url(shop_domain: str | None) -> str | None:
    domain = (shop_domain or "").strip()
    if domain:
        return f"https://{domain}"

    explicit = (settings.shopify_storefront_url or "").strip().rstrip("/")
    if explicit:
        return explicit

    if settings.allowed_shop_domains:
        return f"https://{settings.allowed_shop_domains[0]}"
    return None


def _parse_store_lookup(raw: dict) -> StoreOrderLookup | None:
    if raw.get("Code") != 0:
        return None

    info = ((raw.get("Json") or {}).get("info")) or {}
    order_name = _text(info.get("order_no")) or None
    carrier_info = info.get("carrier_info") or {}
    first_carrier = carrier_info.get("first_carrier_info") or {}
    last_mile = carrier_info.get("last_mile_carrier_info") or {}
    items = []

    for item in info.get("pinfos") or []:
        title = _text(item.get("name"))
        if not title:
            continue
        items.append(
            OrderSummaryItem(
                title=title,
                quantity=int(item.get("count") or 1),
                variant=_text(item.get("variety")) or None,
                imageUrl=_text(item.get("image_url")) or None,
                itemUrl=_text(item.get("item_url")) or None,
                unitPrice=_text(item.get("unit_price")) or None,
                currencyCode=_text(item.get("currency_code")) or None,
            )
        )

    order_summary = None
    if order_name or items:
        order_summary = OrderSummary(
            orderName=order_name,
            totalAmount=_sum_item_prices(info.get("pinfos") or []),
            currencyCode=_text(info.get("currency_code")) or None,
            items=items,
            source="17track_shopify",
        )

    return StoreOrderLookup(
        order_name=order_name,
        carrier_code=_text(first_carrier.get("key")) or None,
        carrier_name=_text(first_carrier.get("name")) or None,
        destination_country=_text(info.get("destCountry")) or None,
        last_mile_tracking_number=_text(last_mile.get("track_no")) or None,
        order_summary=order_summary,
        tracking_params={
            "num": _text(info.get("no")) or None,
            "fc": info.get("fc"),
            "sc": info.get("sc"),
            "g": _text(info.get("g")) or None,
            "params": info.get("params") or {},
        },
        raw_response=raw,
    )


def _sum_item_prices(items: list[dict]) -> str | None:
    total = 0.0
    has_value = False
    for item in items:
        try:
            total += float(item.get("price") or 0) * int(item.get("count") or 1)
            has_value = True
        except (TypeError, ValueError):
            continue
    if not has_value:
        return None
    return f"{total:.2f}"


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
