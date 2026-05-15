from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings
from .schemas import OrderSummary, OrderSummaryItem

ORDER_LOOKUP_QUERY = """
query OrderSummaryByName($query: String!) {
  orders(first: 10, query: $query, sortKey: PROCESSED_AT, reverse: true) {
    nodes {
      id
      name
      email
      processedAt
      displayFulfillmentStatus
      displayFinancialStatus
      currentTotalPriceSet {
        shopMoney {
          amount
          currencyCode
        }
      }
      lineItems(first: 5) {
        nodes {
          name
          quantity
          variantTitle
          image {
            url
          }
          originalUnitPriceSet {
            shopMoney {
              amount
              currencyCode
            }
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class ShopifyTrackingReference:
    tracking_number: str
    carrier_name: str | None = None


@dataclass(frozen=True)
class ShopifyOrderLookup:
    order_summary: OrderSummary
    tracking_numbers: list[ShopifyTrackingReference]
    shipment_pending: bool


class ShopifyAdminClient:
    def __init__(
        self,
        access_token: str | None = None,
        api_version: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.access_token = access_token if access_token is not None else settings.shopify_admin_access_token
        self.api_version = api_version or settings.shopify_admin_api_version
        self.client_id = client_id if client_id is not None else settings.shopify_client_id
        self.client_secret = client_secret if client_secret is not None else settings.shopify_client_secret
        self._token_cache: dict[str, tuple[str, float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.access_token or (self.client_id and self.client_secret))

    def lookup_order_summary(self, shop_domain: str | None, order_name: str | None) -> OrderSummary | None:
        if not self.enabled or not shop_domain or not order_name:
            return None

        orders = self._search_orders(shop_domain, f'name:"{order_name}"')
        if not orders:
            return None
        return _parse_order_summary(orders[0])

    def lookup_order_by_name_and_email(
        self,
        shop_domain: str | None,
        order_name: str | None,
        email: str | None,
    ) -> ShopifyOrderLookup | None:
        if not self.enabled or not shop_domain or not order_name or not email:
            return None

        normalized_order_name = (order_name or "").strip().upper()
        normalized_email = (email or "").strip().lower()
        query = f'name:"{normalized_order_name}" AND email:{normalized_email}'
        orders = self._search_orders(shop_domain, query)
        if not orders:
            return None

        matched = next(
            (
                order
                for order in orders
                if _matches_order_identity(order, normalized_order_name, normalized_email)
            ),
            None,
        )
        if not matched:
            return None

        order_summary = _parse_order_summary(matched)
        order_id = _extract_numeric_order_id(matched.get("id"))
        tracking_numbers = []
        if order_id:
            access_token = self._get_access_token(shop_domain)
            if access_token:
                tracking_numbers = self._fetch_tracking_references(
                    shop_domain,
                    access_token,
                    order_id,
                )

        return ShopifyOrderLookup(
            order_summary=order_summary,
            tracking_numbers=tracking_numbers,
            shipment_pending=not tracking_numbers and _looks_unshipped(order_summary.fulfillment_status),
        )

    def _get_access_token(self, shop_domain: str) -> str | None:
        if self.access_token:
            return self.access_token

        cached = self._token_cache.get(shop_domain)
        if cached and cached[1] > time.time():
            return cached[0]

        if not self.client_id or not self.client_secret:
            return None

        payload = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        request = Request(
            url=f"https://{shop_domain}/admin/oauth/access_token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError):
            return None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None

        token = parsed.get("access_token")
        expires_in = int(parsed.get("expires_in") or 0)
        if not token:
            return None

        ttl = max(expires_in - 300, 60) if expires_in else 3600
        self._token_cache[shop_domain] = (token, time.time() + ttl)
        return token

    def _search_orders(self, shop_domain: str, query: str) -> list[dict[str, Any]]:
        access_token = self._get_access_token(shop_domain)
        if not access_token:
            return []

        parsed = self._post_graphql(
            shop_domain,
            access_token,
            {
                "query": ORDER_LOOKUP_QUERY,
                "variables": {"query": query},
            },
        )
        if not parsed or parsed.get("errors"):
            return []
        return (((parsed.get("data") or {}).get("orders") or {}).get("nodes") or [])

    def _post_graphql(
        self,
        shop_domain: str,
        access_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        request = Request(
            url=f"https://{shop_domain}/admin/api/{self.api_version}/graphql.json",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": access_token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError):
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _fetch_tracking_references(
        self,
        shop_domain: str,
        access_token: str,
        order_id: str,
    ) -> list[ShopifyTrackingReference]:
        request = Request(
            url=(
                f"https://{shop_domain}/admin/api/{self.api_version}/orders/{order_id}.json"
                "?fields=id,name,email,fulfillments"
            ),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": access_token,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError):
            return []

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []

        order = parsed.get("order") or {}
        return _extract_tracking_references(order)


def build_local_order_summary(row: Any) -> OrderSummary | None:
    if not row:
        return None
    return OrderSummary(
        orderName=row["order_name"] or None,
        source=row["source"] or "local",
        items=[],
    )


def merge_order_summaries(primary: OrderSummary | None, fallback: OrderSummary | None) -> OrderSummary | None:
    if not primary:
        return fallback
    if not fallback:
        return primary

    return OrderSummary(
        orderName=primary.order_name or fallback.order_name,
        placedAt=primary.placed_at or fallback.placed_at,
        fulfillmentStatus=primary.fulfillment_status or fallback.fulfillment_status,
        financialStatus=primary.financial_status or fallback.financial_status,
        totalAmount=primary.total_amount or fallback.total_amount,
        currencyCode=primary.currency_code or fallback.currency_code,
        source=primary.source or fallback.source,
        items=_merge_items(primary.items, fallback.items),
    )


def _format_admin_status(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("_", " ").title()


def _parse_order_summary(order: dict[str, Any]) -> OrderSummary:
    price = (((order.get("currentTotalPriceSet") or {}).get("shopMoney")) or {})
    line_items = ((order.get("lineItems") or {}).get("nodes")) or []
    items = []
    for item in line_items:
        unit_price = (((item.get("originalUnitPriceSet") or {}).get("shopMoney")) or {})
        image = item.get("image") or {}
        items.append(
            OrderSummaryItem(
                title=(item.get("name") or "").strip() or "Item",
                quantity=int(item.get("quantity") or 1),
                variant=(item.get("variantTitle") or "").strip() or None,
                imageUrl=(image.get("url") or "").strip() or None,
                unitPrice=_format_unit_price(unit_price.get("amount"), unit_price.get("currencyCode")),
                currencyCode=unit_price.get("currencyCode"),
            )
        )

    return OrderSummary(
        orderName=order.get("name"),
        placedAt=order.get("processedAt"),
        fulfillmentStatus=_format_admin_status(order.get("displayFulfillmentStatus")),
        financialStatus=_format_admin_status(order.get("displayFinancialStatus")),
        totalAmount=price.get("amount"),
        currencyCode=price.get("currencyCode"),
        items=items,
        source="shopify_admin",
    )


def _format_unit_price(amount: str | None, currency_code: str | None) -> str | None:
    if not amount:
        return None
    value = str(amount).strip()
    code = (currency_code or "").strip()
    if code:
        return f"{value} {code}"
    return value or None


def _merge_items(primary_items: list[OrderSummaryItem], fallback_items: list[OrderSummaryItem]) -> list[OrderSummaryItem]:
    if not primary_items:
        return fallback_items
    if not fallback_items:
        return primary_items

    fallback_has_richer_media = any(
        item.image_url or item.item_url or item.unit_price or item.variant
        for item in fallback_items
    )
    if fallback_has_richer_media:
        return fallback_items
    return primary_items


def _matches_order_identity(order: dict[str, Any], order_name: str, email: str) -> bool:
    candidate_name = str(order.get("name") or "").strip().upper()
    candidate_email = str(order.get("email") or "").strip().lower()
    return candidate_name == order_name and candidate_email == email


def _extract_numeric_order_id(global_id: Any) -> str | None:
    text = str(global_id or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    if "/" in text:
        tail = text.rsplit("/", 1)[-1]
        if tail.isdigit():
            return tail
    return None


def _extract_tracking_references(order: dict[str, Any]) -> list[ShopifyTrackingReference]:
    references: list[ShopifyTrackingReference] = []
    seen: set[str] = set()
    for fulfillment in order.get("fulfillments") or []:
        tracking_company = str(fulfillment.get("tracking_company") or "").strip() or None
        tracking_numbers = fulfillment.get("tracking_numbers") or []
        primary_tracking_number = str(fulfillment.get("tracking_number") or "").strip()
        if primary_tracking_number and primary_tracking_number not in tracking_numbers:
            tracking_numbers = [primary_tracking_number, *tracking_numbers]
        for tracking_number in tracking_numbers:
            normalized = str(tracking_number or "").strip().upper()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            references.append(
                ShopifyTrackingReference(
                    tracking_number=normalized,
                    carrier_name=tracking_company,
                )
            )
    return references


def _looks_unshipped(fulfillment_status: str | None) -> bool:
    normalized = str(fulfillment_status or "").strip().lower()
    return normalized in {"", "unfulfilled", "open", "on hold", "scheduled"}
