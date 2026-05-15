from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import settings
from .schemas import OrderSummary, OrderSummaryItem

ORDER_LOOKUP_QUERY = """
query OrderSummaryByName($query: String!) {
  orders(first: 1, query: $query, sortKey: PROCESSED_AT, reverse: true) {
    nodes {
      name
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

        access_token = self._get_access_token(shop_domain)
        if not access_token:
            return None

        payload = {
            "query": ORDER_LOOKUP_QUERY,
            "variables": {"query": f'name:"{order_name}"'},
        }
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
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if parsed.get("errors"):
            return None

        orders = (((parsed.get("data") or {}).get("orders") or {}).get("nodes") or [])
        if not orders:
            return None

        return _parse_order_summary(orders[0])

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
