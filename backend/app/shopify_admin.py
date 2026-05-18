from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings
from .db import fetch_shopify_installation
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


@dataclass(frozen=True)
class ShopifyOrderTrackingMapping:
    order_name: str
    tracking_numbers: list[ShopifyTrackingReference]


@dataclass(frozen=True)
class ShopifyOrderRecord:
    order_name: str
    fulfillment_status: str | None
    tracking_numbers: list[ShopifyTrackingReference]


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
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.access_token or (self.client_id and self.client_secret))

    def lookup_order_summary(self, shop_domain: str | None, order_name: str | None) -> OrderSummary | None:
        if not self.enabled or not shop_domain or not order_name:
            return None

        normalized_order_name = (order_name or "").strip().upper()
        for query in _build_order_queries(normalized_order_name, None):
            orders = self._search_orders(shop_domain, query)
            matched = next(
                (
                    order
                    for order in orders
                    if _matches_order_identity(order, normalized_order_name, None)
                ),
                None,
            )
            if matched:
                return _parse_order_summary(matched)
        return None

    def lookup_order_by_name(
        self,
        shop_domain: str | None,
        order_name: str | None,
        email: str | None = None,
    ) -> ShopifyOrderLookup | None:
        if not self.enabled or not shop_domain or not order_name:
            return None

        normalized_order_name = (order_name or "").strip().upper()
        normalized_email = (email or "").strip().lower()
        matched = None
        for query in _build_order_queries(normalized_order_name, normalized_email):
            orders = self._search_orders(shop_domain, query)
            matched = next(
                (
                    order
                    for order in orders
                    if _matches_order_identity(order, normalized_order_name, normalized_email)
                ),
                None,
            )
            if matched:
                break
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

    def lookup_order_by_name_and_email(
        self,
        shop_domain: str | None,
        order_name: str | None,
        email: str | None,
    ) -> ShopifyOrderLookup | None:
        return self.lookup_order_by_name(shop_domain, order_name, email)

    def lookup_order_by_tracking_number(
        self,
        shop_domain: str | None,
        tracking_number: str | None,
    ) -> ShopifyOrderLookup | None:
        if not self.enabled or not shop_domain or not tracking_number:
            return None

        normalized_tracking_number = str(tracking_number or "").strip().upper()
        if not normalized_tracking_number:
            return None

        orders = self._search_orders(shop_domain, normalized_tracking_number)
        if not orders:
            return None

        access_token = self._get_access_token(shop_domain)
        if not access_token:
            return None

        for order in orders:
            order_id = _extract_numeric_order_id(order.get("id"))
            if not order_id:
                continue
            tracking_references = self._fetch_tracking_references(
                shop_domain,
                access_token,
                order_id,
            )
            if not any(
                reference.tracking_number == normalized_tracking_number
                for reference in tracking_references
            ):
                continue
            order_summary = _parse_order_summary(order)
            return ShopifyOrderLookup(
                order_summary=order_summary,
                tracking_numbers=tracking_references,
                shipment_pending=not tracking_references and _looks_unshipped(order_summary.fulfillment_status),
            )
        return None

    def iter_order_tracking_mappings(
        self,
        shop_domain: str | None,
        *,
        updated_at_min: str | None = None,
        limit: int = 250,
        max_pages: int | None = None,
    ) -> list[ShopifyOrderTrackingMapping]:
        if not self.enabled or not shop_domain:
            if not self.enabled:
                self.last_error = "Shopify Admin integration is not configured."
            return []

        access_token = self._get_access_token(shop_domain)
        if not access_token:
            return []

        page_info: str | None = None
        page_count = 0
        mappings: list[ShopifyOrderTrackingMapping] = []

        while True:
            orders, next_page_info = self._fetch_orders_page(
                shop_domain,
                access_token,
                page_info=page_info,
                updated_at_min=updated_at_min,
                limit=limit,
            )
            for order in orders:
                order_name = str(order.get("name") or "").strip()
                if not order_name:
                    continue
                tracking_numbers = extract_tracking_references(order)
                if not tracking_numbers:
                    continue
                mappings.append(
                    ShopifyOrderTrackingMapping(
                        order_name=order_name,
                        tracking_numbers=tracking_numbers,
                    )
                )

            page_count += 1
            if not next_page_info or (max_pages and page_count >= max_pages):
                break
            page_info = next_page_info

        return mappings

    def iter_orders(
        self,
        shop_domain: str | None,
        *,
        updated_at_min: str | None = None,
        limit: int = 250,
        max_pages: int | None = None,
    ) -> list[ShopifyOrderRecord]:
        if not self.enabled or not shop_domain:
            if not self.enabled:
                self.last_error = "Shopify Admin integration is not configured."
            return []

        access_token = self._get_access_token(shop_domain)
        if not access_token:
            return []

        page_info: str | None = None
        page_count = 0
        records: list[ShopifyOrderRecord] = []

        while True:
            orders, next_page_info = self._fetch_orders_page(
                shop_domain,
                access_token,
                page_info=page_info,
                updated_at_min=updated_at_min,
                limit=limit,
            )
            for order in orders:
                order_name = str(order.get("name") or "").strip()
                if not order_name:
                    continue
                records.append(
                    ShopifyOrderRecord(
                        order_name=order_name,
                        fulfillment_status=_format_admin_status(order.get("fulfillment_status")),
                        tracking_numbers=extract_tracking_references(order),
                    )
                )

            page_count += 1
            if not next_page_info or (max_pages and page_count >= max_pages):
                break
            page_info = next_page_info

        return records

    def _get_access_token(self, shop_domain: str) -> str | None:
        if self.access_token:
            self.last_error = None
            return self.access_token

        installation = fetch_shopify_installation(shop_domain)
        if installation and installation["access_token"]:
            self.last_error = None
            return str(installation["access_token"])

        cached = self._token_cache.get(shop_domain)
        if cached and cached[1] > time.time():
            self.last_error = None
            return cached[0]

        if not self.client_id or not self.client_secret:
            self.last_error = "Missing Shopify client credentials."
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
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.last_error = f"Shopify token exchange failed: HTTP {exc.code} {detail}".strip()
            return None
        except (URLError, TimeoutError):
            self.last_error = "Shopify token exchange network error."
            return None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            self.last_error = "Shopify token exchange returned invalid JSON."
            return None

        token = parsed.get("access_token")
        granted_scope = str(parsed.get("scope") or "").strip()
        expires_in = int(parsed.get("expires_in") or 0)
        if not token:
            self.last_error = "Shopify token exchange returned no access token."
            return None
        if not granted_scope:
            self.last_error = (
                "Shopify token exchange returned an access token without Admin API scopes. "
                "Reauthorize the app through the OAuth install flow."
            )
            return None

        ttl = max(expires_in - 300, 60) if expires_in else 3600
        self._token_cache[shop_domain] = (token, time.time() + ttl)
        self.last_error = None
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

    def _fetch_orders_page(
        self,
        shop_domain: str,
        access_token: str,
        *,
        page_info: str | None = None,
        updated_at_min: str | None = None,
        limit: int = 250,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 250)),
            "fields": "id,name,fulfillment_status,fulfillments",
        }
        if page_info:
            params["page_info"] = page_info
        else:
            params["status"] = "any"
            if updated_at_min:
                params["updated_at_min"] = updated_at_min
                params["order"] = "updated_at asc"

        request = Request(
            url=(
                f"https://{shop_domain}/admin/api/{self.api_version}/orders.json"
                f"?{urlencode(params)}"
            ),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": access_token,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                link_header = response.headers.get("Link")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self.last_error = f"Shopify orders page request failed: HTTP {exc.code} {detail}".strip()
            return [], None
        except (URLError, TimeoutError):
            self.last_error = "Shopify orders page request failed due to a network error."
            return [], None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            self.last_error = "Shopify orders page returned invalid JSON."
            return [], None

        orders = parsed.get("orders") or []
        self.last_error = None
        return orders, _extract_next_page_info(link_header)

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
        order = self.fetch_order_payload(shop_domain, order_id, access_token=access_token)
        if not order:
            return []
        return extract_tracking_references(order)

    def fetch_order_payload(
        self,
        shop_domain: str | None,
        order_id: str | int | None,
        *,
        access_token: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled or not shop_domain or not order_id:
            return None

        token = access_token or self._get_access_token(shop_domain)
        if not token:
            return None

        request = Request(
            url=(
                f"https://{shop_domain}/admin/api/{self.api_version}/orders/{order_id}.json"
                "?fields=id,name,email,fulfillments,displayFulfillmentStatus,displayFinancialStatus,currentTotalPriceSet,line_items"
            ),
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": token,
            },
            method="GET",
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

        return parsed.get("order") or None


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


def _normalize_order_identity(value: str | None) -> str:
    return str(value or "").strip().upper().lstrip("#")


def _build_order_queries(order_name: str, email: str | None) -> list[str]:
    normalized = (order_name or "").strip().upper()
    if not normalized:
        return []
    variants = [normalized]
    if normalized.startswith("#"):
        variants.append(normalized.lstrip("#"))
    else:
        variants.append(f"#{normalized}")
    queries: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        for query in (
            f'name:"{variant}"',
            variant,
        ):
            if email:
                query = f"{query} AND email:{email}"
            if query in seen:
                continue
            seen.add(query)
            queries.append(query)
    return queries


def _matches_order_identity(order: dict[str, Any], order_name: str, email: str | None) -> bool:
    candidate_name = _normalize_order_identity(order.get("name"))
    target_name = _normalize_order_identity(order_name)
    candidate_email = str(order.get("email") or "").strip().lower()
    if candidate_name != target_name:
        return False
    if email:
        return candidate_email == email
    return True


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


def extract_tracking_references(order: dict[str, Any]) -> list[ShopifyTrackingReference]:
    return _extract_tracking_references(order)


def _extract_next_page_info(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue
        start = section.find("<")
        end = section.find(">")
        if start == -1 or end == -1 or end <= start + 1:
            continue
        parsed = urlparse(section[start + 1 : end])
        page_info = parse_qs(parsed.query).get("page_info", [None])[0]
        if page_info:
            return page_info
    return None


def _looks_unshipped(fulfillment_status: str | None) -> bool:
    normalized = str(fulfillment_status or "").strip().lower()
    return normalized in {"", "unfulfilled", "open", "on hold", "scheduled"}
