from __future__ import annotations

import base64
import json
import hashlib
import hmac
import shutil
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import config as config_module
from backend.app import main as main_module
from backend.app.db import (
    count_recent_system_events,
    fetch_shopify_installation,
    fetch_tracking_record,
    init_db,
    is_store_order_tracking_number,
    list_order_tracking_numbers_for_order_names,
    summarize_daily_usage,
    upsert_order_tracking_number,
    upsert_tracking_record,
)
from backend.app.normalization import normalize_status, status_label
from backend.app.observability import (
    _build_alert_payload,
    _sign_feishu,
    monitor_event_spike,
    send_daily_usage_report,
)
from backend.app.seventeen_track import parse_track_info
from backend.app import seventeen_track_storefront as storefront_module
from backend.app import services as services_module
from backend.app import shopify_admin as shopify_admin_module
from backend.app import shopify_webhooks as webhooks_module
from backend.app import shopify_oauth as oauth_module
from backend.app.schemas import OrderSummary, OrderSummaryItem
from backend.tools.import_order_trackings import (
    _normalize_header,
    _pick_column,
    _should_use_positional_fallback,
    _split_tracking_values,
)
from backend.app.services import (
    is_valid_order_number,
    parse_tracking_numbers,
    process_tracking_number,
    query_order_tracking,
)


@contextmanager
def workspace_temp_dir():
    root = Path.cwd() / ".tmp_tests"
    root.mkdir(exist_ok=True)
    temp_dir = root / f"case-{uuid4().hex}"
    temp_dir.mkdir()
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class CoreTests(unittest.TestCase):
    def test_storefront_resolution_prefers_incoming_shop_domain(self) -> None:
        original_slug = config_module.settings.shopify_store_slug
        original_url = config_module.settings.shopify_storefront_url
        try:
            object.__setattr__(config_module.settings, "shopify_store_slug", "lintico-uk")
            object.__setattr__(
                config_module.settings,
                "shopify_storefront_url",
                "https://linticoshop.uk",
            )
            self.assertEqual(
                storefront_module._resolve_shop_slug("v2npww-33.myshopify.com"),
                "v2npww-33",
            )
            self.assertEqual(
                storefront_module._resolve_storefront_url("v2npww-33.myshopify.com"),
                "https://v2npww-33.myshopify.com",
            )
        finally:
            object.__setattr__(config_module.settings, "shopify_store_slug", original_slug)
            object.__setattr__(config_module.settings, "shopify_storefront_url", original_url)

    def test_feishu_signature_generation(self) -> None:
        self.assertEqual(
            _sign_feishu("1710000000", "JvpXdHBvOh8gnz4dr1a52e"),
            "JeqJtm5Pj0/7qMksTQwfnAE4c1iOA99vz9+yBRATU2o=",
        )

    def test_build_alert_payload_for_feishu(self) -> None:
        original_url = config_module.settings.alert_webhook_url
        original_secret = config_module.settings.alert_webhook_secret
        try:
            object.__setattr__(
                config_module.settings,
                "alert_webhook_url",
                "https://open.feishu.cn/open-apis/bot/v2/hook/demo",
            )
            object.__setattr__(
                config_module.settings,
                "alert_webhook_secret",
                "demo-secret",
            )
            payload = _build_alert_payload(
                "storefront_tracking_failed",
                "error",
                "17TRACK storefront tracking detail lookup failed.",
                {"tracking_number": "4PX3002735874120CN"},
            )
            self.assertEqual(payload["msg_type"], "text")
            self.assertIn("tracking-alert", payload["content"]["text"])
            self.assertIn("timestamp", payload)
            self.assertIn("sign", payload)
        finally:
            object.__setattr__(config_module.settings, "alert_webhook_url", original_url)
            object.__setattr__(config_module.settings, "alert_webhook_secret", original_secret)

    def test_monitor_event_spike_counts_recent_events(self) -> None:
        original_db_path = config_module.settings.database_path
        original_url = config_module.settings.alert_webhook_url
        original_interval = config_module.settings.alert_min_interval_seconds
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                object.__setattr__(config_module.settings, "alert_webhook_url", "")
                object.__setattr__(config_module.settings, "alert_min_interval_seconds", 60)
                init_db()

                services_module.log_event("tracking_not_store_order", level="warning", shop_domain="demo.myshopify.com")
                services_module.log_event("order_lookup_not_found", level="warning", shop_domain="demo.myshopify.com")

                count = count_recent_system_events(
                    ("tracking_not_store_order", "order_lookup_not_found"),
                    300,
                )
                self.assertEqual(count, 2)

                monitor_event_spike(
                    source_events=("tracking_not_store_order", "order_lookup_not_found"),
                    alert_event="not_store_order_spike",
                    threshold=2,
                    window_seconds=300,
                    message="Store-order validation rejections spiked within the alert window.",
                    shop_domain="demo.myshopify.com",
                )

                self.assertGreaterEqual(
                    count_recent_system_events("alert_delivery_failed", 300),
                    0,
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                object.__setattr__(config_module.settings, "alert_webhook_url", original_url)
                object.__setattr__(config_module.settings, "alert_min_interval_seconds", original_interval)

    def test_shopify_oauth_start_redirects_to_authorize(self) -> None:
        original_client_id = config_module.settings.shopify_client_id
        original_client_secret = config_module.settings.shopify_client_secret
        original_allowed_domains = config_module.settings.allowed_shop_domains
        try:
            object.__setattr__(config_module.settings, "shopify_client_id", "demo-client-id")
            object.__setattr__(config_module.settings, "shopify_client_secret", "demo-client-secret")
            object.__setattr__(config_module.settings, "allowed_shop_domains", ("demo.myshopify.com",))
            client = TestClient(main_module.app, base_url="https://testserver")
            response = client.get(
                "/api/shopify/auth/start",
                params={"shop": "demo.myshopify.com"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn("admin/oauth/authorize", response.headers["location"])
            self.assertIn("client_id=demo-client-id", response.headers["location"])
            self.assertIn("scope=read_orders%2Cread_fulfillments%2Cread_all_orders", response.headers["location"])
            self.assertIn("shopify_oauth_nonce", response.headers.get("set-cookie", ""))
        finally:
            object.__setattr__(config_module.settings, "shopify_client_id", original_client_id)
            object.__setattr__(config_module.settings, "shopify_client_secret", original_client_secret)
            object.__setattr__(config_module.settings, "allowed_shop_domains", original_allowed_domains)

    def test_shopify_oauth_callback_stores_installation(self) -> None:
        original_db_path = config_module.settings.database_path
        original_client_id = config_module.settings.shopify_client_id
        original_client_secret = config_module.settings.shopify_client_secret
        original_allowed_domains = config_module.settings.allowed_shop_domains
        original_exchange = oauth_module.exchange_code_for_offline_token
        original_main_exchange = main_module.exchange_code_for_offline_token
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                object.__setattr__(config_module.settings, "shopify_client_id", "demo-client-id")
                object.__setattr__(config_module.settings, "shopify_client_secret", "demo-client-secret")
                object.__setattr__(config_module.settings, "allowed_shop_domains", ("demo.myshopify.com",))
                init_db()
                oauth_module.exchange_code_for_offline_token = lambda *_args, **_kwargs: {
                    "access_token": "shpat_demo",
                    "scope": "read_orders,read_fulfillments,read_all_orders",
                }
                main_module.exchange_code_for_offline_token = oauth_module.exchange_code_for_offline_token

                client = TestClient(main_module.app, base_url="https://testserver")
                start_response = client.get(
                    "/api/shopify/auth/start",
                    params={"shop": "demo.myshopify.com"},
                    follow_redirects=False,
                )
                redirect_url = start_response.headers["location"]
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(redirect_url)
                state = parse_qs(parsed.query)["state"][0]
                query = {
                    "code": "demo-code",
                    "shop": "demo.myshopify.com",
                    "state": state,
                    "timestamp": str(int(__import__("time").time())),
                }
                message = "&".join(f"{key}={value}" for key, value in sorted(query.items()))
                query["hmac"] = hmac.new(
                    config_module.settings.shopify_client_secret.encode("utf-8"),
                    message.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                callback_response = client.get("/api/shopify/auth/callback", params=query)
                self.assertEqual(callback_response.status_code, 200)
                installation = fetch_shopify_installation("demo.myshopify.com")
                self.assertIsNotNone(installation)
                self.assertEqual(installation["access_token"], "shpat_demo")
                self.assertIn("read_all_orders", installation["scope"])
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                object.__setattr__(config_module.settings, "shopify_client_id", original_client_id)
                object.__setattr__(config_module.settings, "shopify_client_secret", original_client_secret)
                object.__setattr__(config_module.settings, "allowed_shop_domains", original_allowed_domains)
                oauth_module.exchange_code_for_offline_token = original_exchange
                main_module.exchange_code_for_offline_token = original_main_exchange

    def test_shopify_admin_prefers_stored_installation_token(self) -> None:
        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                from backend.app.db import upsert_shopify_installation

                upsert_shopify_installation(
                    "demo.myshopify.com",
                    "shpat_demo_installation",
                    "read_orders,read_fulfillments,read_all_orders",
                )
                client = shopify_admin_module.ShopifyAdminClient(
                    access_token="",
                    client_id="",
                    client_secret="",
                )
                self.assertEqual(
                    client._get_access_token("demo.myshopify.com"),
                    "shpat_demo_installation",
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_shopify_admin_empty_scoped_client_credentials_sets_error(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        original_db_path = config_module.settings.database_path
        original_urlopen = shopify_admin_module.urlopen
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                shopify_admin_module.urlopen = lambda *_args, **_kwargs: FakeResponse(
                    {
                        "access_token": "shpat_empty",
                        "scope": "",
                        "expires_in": 3600,
                    }
                )
                client = shopify_admin_module.ShopifyAdminClient(
                    access_token="",
                    client_id="demo-client-id",
                    client_secret="demo-client-secret",
                )
                mappings = client.iter_order_tracking_mappings("demo.myshopify.com")
                self.assertEqual(mappings, [])
                self.assertIn("without Admin API scopes", client.last_error or "")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                shopify_admin_module.urlopen = original_urlopen

    def test_fetch_tracking_record_without_carrier_uses_detected_carrier(self) -> None:
        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_tracking_record(
                    {
                        "tracking_number": "YT2610601001467359",
                        "carrier_code": "190008",
                        "carrier_name": "YunExpress",
                        "shop_domain": "",
                        "is_registered": True,
                        "normalized_status": "in_transit",
                        "status_text": "In transit",
                        "provider_status": "InTransit",
                        "provider_status_description": "Package departed facility",
                        "origin_country": "CN",
                        "destination_country": "GB",
                        "last_event_time": "2026-05-08T02:00:00+00:00",
                        "last_fetched_at": "2026-05-08T02:10:00+00:00",
                        "cache_expires_at": "2026-05-08T04:10:00+00:00",
                        "events": [],
                        "raw_response": {},
                    }
                )
                record = fetch_tracking_record("YT2610601001467359", None)
                self.assertIsNotNone(record)
                self.assertEqual(record["carrier_code"], "190008")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_process_tracking_number_retries_with_detected_carrier(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None]] = []

            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.calls.append(("register", carrier_code))
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.calls.append(("get", carrier_code))
                if carrier_code is None:
                    return {
                        "data": {
                            "accepted": [
                                {
                                    "number": tracking_number,
                                    "carrier": "190008",
                                    "track": {
                                        "z0": "NotFound",
                                        "z1": "No tracking updates",
                                        "tracking": [],
                                    },
                                }
                            ]
                        }
                    }
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code,
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Departed origin facility",
                                    "tracking": [
                                        {
                                            "eventTime": "2026-05-08T03:00:00+00:00",
                                            "location": "Shenzhen, CN",
                                            "description": "Departed origin facility",
                                            "status": "InTransit",
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                }

        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                shipment, error = process_tracking_number(
                    FakeClient(),
                    "YT2610601001467359",
                    None,
                    None,
                    enforce_order_match=False,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(shipment)
                self.assertEqual(shipment.carrier_code, "190008")
                self.assertEqual(shipment.normalized_status, "in_transit")
                self.assertEqual(len(shipment.events), 1)
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_process_tracking_number_attaches_local_order_summary(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "190008",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [
                                        {
                                            "eventTime": "2026-05-08T03:00:00+00:00",
                                            "location": "Shenzhen, CN",
                                            "description": "Parcel moving",
                                            "status": "InTransit",
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                }

        original_db_path = config_module.settings.database_path
        original_admin_token = services_module.shopify_admin_client.access_token
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                services_module.shopify_admin_client.access_token = ""
                init_db()
                upsert_order_tracking_number(
                    tracking_number="YT2610601001467359",
                    carrier_code="190008",
                    shop_domain="demo.myshopify.com",
                    order_name="#1001",
                    source="manual",
                )
                shipment, error = process_tracking_number(
                    FakeClient(),
                    "YT2610601001467359",
                    "190008",
                    "demo.myshopify.com",
                    enforce_order_match=False,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(shipment)
                self.assertIsNotNone(shipment.order_summary)
                self.assertEqual(shipment.order_summary.order_name, "#1001")
                self.assertEqual(shipment.order_summary.source, "manual")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.access_token = original_admin_token

    def test_process_tracking_number_uses_storefront_lookup(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "190094",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [
                                        {
                                            "eventTime": "2026-05-12T03:00:00+00:00",
                                            "location": "Manchester, GB",
                                            "description": "Parcel moving",
                                            "status": "InTransit",
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                }

        class FakeStorefrontLookup:
            order_name = "LUK2806"
            carrier_code = "190094"
            carrier_name = "4PX"
            destination_country = "GB"
            tracking_params = {"fc": 190094, "g": "demo-guid", "num": "4PX3002754801725CN", "sc": 0, "params": {}}
            shipment_pending = False
            order_summary = services_module.build_local_order_summary(
                {
                    "order_name": "LUK2806",
                    "source": "17track_shopify",
                }
            )

        original_db_path = config_module.settings.database_path
        original_lookup = services_module.storefront_client.lookup_by_tracking
        original_detail_lookup = services_module.storefront_client.fetch_tracking_detail
        original_admin_token = services_module.shopify_admin_client.access_token
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                services_module.storefront_client.lookup_by_tracking = lambda *_args, **_kwargs: FakeStorefrontLookup()
                services_module.storefront_client.fetch_tracking_detail = (
                    lambda *_args, **_kwargs: {
                        "shipments": [
                            {
                                "code": 200,
                                "number": "4PX3002754801725CN",
                                "carrier": 190094,
                                "shipment": {
                                    "shipping_info": {
                                        "shipper_address": {"country": "CN"},
                                        "recipient_address": {"country": "GB"},
                                    },
                                    "latest_status": {"status": "InTransit"},
                                    "latest_event": {
                                        "time_utc": "2026-05-12T23:59:44Z",
                                        "description": "Depart from facility to service provider.",
                                        "location": "Nancheng",
                                    },
                                    "tracking": {
                                        "providers": [
                                            {
                                                "provider": {"key": 190094, "name": "4PX"},
                                                "events": [
                                                    {
                                                        "time_utc": "2026-05-12T23:59:44Z",
                                                        "description": "Depart from facility to service provider.",
                                                        "location": "Nancheng",
                                                        "stage": "InTransit",
                                                    }
                                                ],
                                            }
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                )
                services_module.shopify_admin_client.access_token = ""
                init_db()
                shipment, error = process_tracking_number(
                    FakeClient(),
                    "4PX3002754801725CN",
                    None,
                    "demo.myshopify.com",
                    enforce_order_match=True,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(shipment)
                self.assertEqual(shipment.carrier_code, "190094")
                self.assertEqual(shipment.carrier_name, "4PX")
                self.assertEqual(shipment.destination_country, "GB")
                self.assertEqual(shipment.normalized_status, "in_transit")
                self.assertEqual(len(shipment.events), 1)
                self.assertIsNotNone(shipment.order_summary)
                self.assertEqual(shipment.order_summary.order_name, "LUK2806")
                self.assertTrue(
                    is_store_order_tracking_number(
                        "4PX3002754801725CN",
                        "190094",
                        "demo.myshopify.com",
                    )
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.storefront_client.lookup_by_tracking = original_lookup
                services_module.storefront_client.fetch_tracking_detail = original_detail_lookup
                services_module.shopify_admin_client.access_token = original_admin_token

    def test_parse_track_info_supports_shopify_track_shipments_payload(self) -> None:
        raw = {
            "shipments": [
                {
                    "code": 200,
                    "number": "4PX3002754801725CN",
                    "carrier": 190094,
                    "shipment": {
                        "shipping_info": {
                            "shipper_address": {"country": "CN"},
                            "recipient_address": {"country": "GB"},
                        },
                        "latest_status": {"status": "InTransit"},
                        "latest_event": {
                            "time_utc": "2026-05-12T23:59:44Z",
                            "description": "Depart from facility to service provider.",
                            "location": "Nancheng",
                        },
                        "tracking": {
                            "providers": [
                                {
                                    "provider": {"key": 190094, "name": "4PX"},
                                    "events": [
                                        {
                                            "time_utc": "2026-05-12T23:59:44Z",
                                            "description": "Depart from facility to service provider.",
                                            "location": "Nancheng",
                                            "stage": "InTransit",
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                }
            ]
        }
        parsed = parse_track_info(raw, "4PX3002754801725CN")
        self.assertEqual(parsed["carrier_code"], "190094")
        self.assertEqual(parsed["carrier_name"], "4PX")
        self.assertEqual(parsed["normalized_status"], "in_transit")
        self.assertEqual(parsed["destination_country"], "GB")
        self.assertEqual(len(parsed["events"]), 1)

    def test_query_order_tracking_uses_storefront_order_lookup(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "190094",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        class FakeStorefrontLookup:
            order_name = "LUK2806"
            carrier_code = "190094"
            carrier_name = "4PX"
            destination_country = "GB"
            last_mile_tracking_number = "JJD0002234523168744"
            shipment_pending = False
            tracking_params = {"fc": 190094, "g": "demo-guid", "num": "4PX3002754801725CN", "sc": 0, "params": {}}
            order_summary = OrderSummary(
                orderName="LUK2806",
                items=[
                    OrderSummaryItem(
                        title="100% Linen Sleeveless Dress SIENNA",
                        quantity=1,
                        imageUrl="https://example.com/item.jpg",
                        unitPrice="81.90 GBP",
                    )
                ],
                source="17track_shopify",
            )

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_detail_lookup = services_module.storefront_client.fetch_tracking_detail
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                services_module.shopify_admin_client.lookup_order_by_name_and_email = lambda *_args, **_kwargs: None
                services_module.storefront_client.lookup_by_order = lambda *_args, **_kwargs: FakeStorefrontLookup()
                services_module.storefront_client.fetch_tracking_detail = (
                    lambda *_args, **_kwargs: {
                        "shipments": [
                            {
                                "code": 200,
                                "number": "4PX3002754801725CN",
                                "carrier": 190094,
                                "shipment": {
                                    "shipping_info": {
                                        "shipper_address": {"country": "CN"},
                                        "recipient_address": {"country": "GB"},
                                    },
                                    "latest_status": {"status": "InTransit"},
                                    "latest_event": {
                                        "time_utc": "2026-05-12T23:59:44Z",
                                        "description": "Depart from facility to service provider.",
                                        "location": "Nancheng",
                                    },
                                    "tracking": {
                                        "providers": [
                                            {
                                                "provider": {"key": 190094, "name": "4PX"},
                                                "events": [
                                                    {
                                                        "time_utc": "2026-05-12T23:59:44Z",
                                                        "description": "Depart from facility to service provider.",
                                                        "location": "Nancheng",
                                                        "stage": "InTransit",
                                                    }
                                                ],
                                            }
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                )
                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LUK2806",
                    "doyle.sj@outlook.com",
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(len(shipments), 1)
                self.assertEqual(shipments[0].tracking_number, "4PX3002754801725CN")
                self.assertEqual(shipments[0].carrier_name, "4PX")
                self.assertEqual(shipments[0].last_mile_tracking_number, "JJD0002234523168744")
                self.assertIsNotNone(shipments[0].order_summary)
                self.assertEqual(shipments[0].order_summary.order_name, "LUK2806")
                self.assertEqual(len(shipments[0].order_summary.items), 1)
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup
                services_module.storefront_client.lookup_by_order = original_lookup_by_order
                services_module.storefront_client.fetch_tracking_detail = original_detail_lookup

    def test_query_order_tracking_handles_unshipped_placeholder(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.fail("register should not be called for an unshipped order")

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.fail("get_track_info should not be called for an unshipped order")

        class FakeStorefrontLookup:
            order_name = "LUK3999"
            carrier_code = None
            carrier_name = None
            destination_country = "GB"
            last_mile_tracking_number = None
            shipment_pending = True
            tracking_params = {"fc": None, "g": None, "num": None, "sc": 0, "params": {}}
            order_summary = OrderSummary(orderName="LUK3999", source="17track_shopify")

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_token = services_module.shopify_admin_client.access_token
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                services_module.shopify_admin_client.lookup_order_by_name_and_email = lambda *_args, **_kwargs: None
                services_module.storefront_client.lookup_by_order = lambda *_args, **_kwargs: FakeStorefrontLookup()
                services_module.shopify_admin_client.access_token = ""
                init_db()
                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LUK3999",
                    "demo@example.com",
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(len(shipments), 1)
                self.assertEqual(shipments[0].tracking_number, "")
                self.assertEqual(shipments[0].status_text, "Not shipped yet")
                self.assertEqual(shipments[0].provider_status, "Not shipped")
                self.assertEqual(shipments[0].order_summary.order_name, "LUK3999")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup
                services_module.storefront_client.lookup_by_order = original_lookup_by_order
                services_module.shopify_admin_client.access_token = original_admin_token

    def test_query_order_tracking_prefers_shopify_admin_lookup_with_tracking(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "4PX",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        admin_lookup = shopify_admin_module.ShopifyOrderLookup(
            order_summary=OrderSummary(orderName="LC8100320", fulfillmentStatus="Fulfilled", source="shopify_admin"),
            tracking_numbers=[
                shopify_admin_module.ShopifyTrackingReference(
                    tracking_number="4PX3001999027341CN",
                    carrier_name="4PX",
                ),
                shopify_admin_module.ShopifyTrackingReference(
                    tracking_number="YT2613500705594269",
                    carrier_name="YunExpress",
                )
            ],
            shipment_pending=False,
        )

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                services_module.shopify_admin_client.lookup_order_by_name_and_email = (
                    lambda *_args, **_kwargs: admin_lookup
                )
                services_module.storefront_client.lookup_by_order = (
                    lambda *_args, **_kwargs: self.fail("storefront order lookup should not run when Shopify Admin already matched")
                )
                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LC8100320",
                    "demo@example.com",
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(len(shipments), 2)
                self.assertEqual(
                    [shipment.tracking_number for shipment in shipments],
                    ["4PX3001999027341CN", "YT2613500705594269"],
                )
                self.assertTrue(
                    is_store_order_tracking_number(
                        "4PX3001999027341CN",
                        None,
                        "demo.myshopify.com",
                    )
                )
                self.assertTrue(
                    is_store_order_tracking_number(
                        "YT2613500705594269",
                        None,
                        "demo.myshopify.com",
                    )
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup
                services_module.storefront_client.lookup_by_order = original_lookup_by_order

    def test_process_tracking_number_uses_shopify_admin_lookup_for_store_ownership(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "190008",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        admin_lookup = shopify_admin_module.ShopifyOrderLookup(
            order_summary=OrderSummary(orderName="LC8124076", fulfillmentStatus="Fulfilled", source="shopify_admin"),
            tracking_numbers=[
                shopify_admin_module.ShopifyTrackingReference(
                    tracking_number="YT2603300702516080",
                    carrier_name="YunExpress",
                )
            ],
            shipment_pending=False,
        )

        original_db_path = config_module.settings.database_path
        original_storefront_lookup = services_module.storefront_client.lookup_by_tracking
        original_admin_tracking_lookup = services_module.shopify_admin_client.lookup_order_by_tracking_number
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                services_module.storefront_client.lookup_by_tracking = lambda *_args, **_kwargs: None
                services_module.shopify_admin_client.lookup_order_by_tracking_number = (
                    lambda *_args, **_kwargs: admin_lookup
                )
                shipment, error = process_tracking_number(
                    FakeClient(),
                    "YT2603300702516080",
                    None,
                    "demo.myshopify.com",
                    enforce_order_match=True,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(shipment)
                self.assertEqual(shipment.tracking_number, "YT2603300702516080")
                self.assertTrue(
                    is_store_order_tracking_number(
                        "YT2603300702516080",
                        None,
                        "demo.myshopify.com",
                    )
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.storefront_client.lookup_by_tracking = original_storefront_lookup
                services_module.shopify_admin_client.lookup_order_by_tracking_number = original_admin_tracking_lookup

    def test_query_order_tracking_falls_back_to_local_mapping(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "auto",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_order_tracking_number(
                    tracking_number="YT2602700701984711",
                    carrier_code="",
                    shop_domain="demo.myshopify.com",
                    order_name="LFR1112",
                    source="historical_import",
                )
                services_module.shopify_admin_client.lookup_order_by_name_and_email = lambda *_args, **_kwargs: None
                services_module.storefront_client.lookup_by_order = lambda *_args, **_kwargs: None

                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LFR1112",
                    None,
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual([shipment.tracking_number for shipment in shipments], ["YT2602700701984711"])
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.storefront_client.lookup_by_order = original_lookup_by_order
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup

    def test_query_order_tracking_returns_all_local_mapped_split_shipments(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "auto",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_order_tracking_number(
                    tracking_number="YT2611001002223456",
                    carrier_code="",
                    shop_domain="demo.myshopify.com",
                    order_name="LC8152308",
                    source="shopify_backfill",
                )
                upsert_order_tracking_number(
                    tracking_number="YT2611001002216716",
                    carrier_code="",
                    shop_domain="demo.myshopify.com",
                    order_name="LC8152308",
                    source="shopify_backfill",
                )
                services_module.shopify_admin_client.lookup_order_by_name_and_email = lambda *_args, **_kwargs: None
                services_module.storefront_client.lookup_by_order = lambda *_args, **_kwargs: None

                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LC8152308",
                    None,
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(
                    sorted(shipment.tracking_number for shipment in shipments),
                    ["YT2611001002216716", "YT2611001002223456"],
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.storefront_client.lookup_by_order = original_lookup_by_order
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup

    def test_query_order_tracking_appends_unshipped_remainder_for_split_fulfillment(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "4PX",
                                "track": {
                                    "z0": "InTransit",
                                    "z1": "Parcel moving",
                                    "tracking": [],
                                },
                            }
                        ]
                    }
                }

        admin_lookup = shopify_admin_module.ShopifyOrderLookup(
            order_summary=OrderSummary(
                orderName="LC8100320",
                fulfillmentStatus="Partially Fulfilled",
                source="shopify_admin",
            ),
            tracking_numbers=[
                shopify_admin_module.ShopifyTrackingReference(
                    tracking_number="4PX3001999027341CN",
                    carrier_name="4PX",
                )
            ],
            shipment_pending=False,
        )

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                services_module.shopify_admin_client.lookup_order_by_name_and_email = (
                    lambda *_args, **_kwargs: admin_lookup
                )
                services_module.storefront_client.lookup_by_order = (
                    lambda *_args, **_kwargs: self.fail("storefront order lookup should not run when Shopify Admin already matched")
                )
                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LC8100320",
                    None,
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(len(shipments), 2)
                self.assertEqual(shipments[0].tracking_number, "4PX3001999027341CN")
                self.assertEqual(shipments[1].tracking_number, "")
                self.assertEqual(shipments[1].status_text, "Not shipped yet")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup
                services_module.storefront_client.lookup_by_order = original_lookup_by_order

    def test_query_order_tracking_returns_unshipped_shopify_admin_order(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.fail("register should not be called for an unshipped Shopify order")

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                self.fail("get_track_info should not be called for an unshipped Shopify order")

        admin_lookup = shopify_admin_module.ShopifyOrderLookup(
            order_summary=OrderSummary(orderName="LC999999", fulfillmentStatus="Unfulfilled", source="shopify_admin"),
            tracking_numbers=[],
            shipment_pending=True,
        )

        original_db_path = config_module.settings.database_path
        original_lookup_by_order = services_module.storefront_client.lookup_by_order
        original_admin_lookup = services_module.shopify_admin_client.lookup_order_by_name_and_email
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                services_module.shopify_admin_client.lookup_order_by_name_and_email = (
                    lambda *_args, **_kwargs: admin_lookup
                )
                services_module.storefront_client.lookup_by_order = (
                    lambda *_args, **_kwargs: self.fail("storefront order lookup should not run when Shopify Admin marked order unshipped")
                )
                shipments, errors = query_order_tracking(
                    FakeClient(),
                    "LC999999",
                    "demo@example.com",
                    "demo.myshopify.com",
                )
                self.assertFalse(errors)
                self.assertEqual(len(shipments), 1)
                self.assertEqual(shipments[0].tracking_number, "")
                self.assertEqual(shipments[0].status_text, "Not shipped yet")
                self.assertEqual(shipments[0].order_summary.order_name, "LC999999")
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.shopify_admin_client.lookup_order_by_name_and_email = original_admin_lookup
                services_module.storefront_client.lookup_by_order = original_lookup_by_order

    def test_process_tracking_number_refreshes_empty_unknown_cache_without_fc(self) -> None:
        class FakeClient:
            def register(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}

            def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
                return {
                    "data": {
                        "accepted": [
                            {
                                "number": tracking_number,
                                "carrier": carrier_code or "auto",
                                "track": {
                                    "z0": "Delivered",
                                    "z1": "Delivered at front desk",
                                    "tracking": [
                                        {
                                            "eventTime": "2026-05-12T13:43:00Z",
                                            "location": "Front desk",
                                            "description": "Delivered at front desk",
                                            "status": "Delivered",
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                }

        class FakeStorefrontLookup:
            order_name = "LC8100320"
            carrier_code = None
            carrier_name = None
            destination_country = "US"
            last_mile_tracking_number = None
            shipment_pending = False
            tracking_params = {"fc": 0, "g": None, "num": "4PX3001999027341CN", "sc": 0, "params": {}}
            order_summary = OrderSummary(orderName="LC8100320", source="17track_shopify")

        original_db_path = config_module.settings.database_path
        original_lookup = services_module.storefront_client.lookup_by_tracking
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_tracking_record(
                    {
                        "tracking_number": "4PX3001999027341CN",
                        "carrier_code": "",
                        "carrier_name": None,
                        "shop_domain": "2vnpww-33.myshopify.com",
                        "is_registered": True,
                        "normalized_status": "unknown",
                        "status_text": "No tracking updates",
                        "provider_status": None,
                        "provider_status_description": "No tracking updates",
                        "origin_country": None,
                        "destination_country": "US",
                        "last_event_time": None,
                        "last_fetched_at": "2026-05-15T08:10:00+00:00",
                        "cache_expires_at": "2099-05-15T10:10:00+00:00",
                        "events": [],
                        "raw_response": {},
                    }
                )
                services_module.storefront_client.lookup_by_tracking = lambda *_args, **_kwargs: FakeStorefrontLookup()
                shipment, error = process_tracking_number(
                    FakeClient(),
                    "4PX3001999027341CN",
                    None,
                    "2vnpww-33.myshopify.com",
                    enforce_order_match=True,
                )
                self.assertIsNone(error)
                self.assertIsNotNone(shipment)
                self.assertEqual(shipment.normalized_status, "delivered")
                self.assertEqual(len(shipment.events), 1)
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                services_module.storefront_client.lookup_by_tracking = original_lookup

    def test_store_order_match_without_carrier_accepts_specific_carrier_row(self) -> None:
        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_order_tracking_number(
                    tracking_number="YT2610601001467359",
                    carrier_code="190008",
                    shop_domain="demo.myshopify.com",
                    order_name="#1001",
                    source="manual",
                )
                self.assertTrue(
                    is_store_order_tracking_number(
                        "YT2610601001467359",
                        None,
                        "demo.myshopify.com",
                    )
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_internal_api_requires_token(self) -> None:
        original_token = config_module.settings.internal_dashboard_token
        original_mock = config_module.settings.mock_when_api_key_missing
        original_db_path = config_module.settings.database_path
        original_client_key = main_module.client.api_key
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "internal_dashboard_token", "secret-token")
                object.__setattr__(config_module.settings, "mock_when_api_key_missing", True)
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                main_module.client.api_key = ""
                init_db()
                with TestClient(main_module.app) as client:
                    response = client.post("/internal/api/track", json={"nums": "RJ556381428CN"})
                self.assertEqual(response.status_code, 401)
            finally:
                object.__setattr__(config_module.settings, "internal_dashboard_token", original_token)
                object.__setattr__(config_module.settings, "mock_when_api_key_missing", original_mock)
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                main_module.client.api_key = original_client_key

    def test_internal_recent_returns_cached_shipments(self) -> None:
        original_token = config_module.settings.internal_dashboard_token
        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "internal_dashboard_token", "secret-token")
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_tracking_record(
                    {
                        "tracking_number": "RJ556381428CN",
                        "carrier_code": "3011",
                        "carrier_name": "YunExpress",
                        "shop_domain": "",
                        "is_registered": True,
                        "normalized_status": "in_transit",
                        "status_text": "In transit",
                        "provider_status": "InTransit",
                        "provider_status_description": "Parcel moving to destination",
                        "origin_country": "CN",
                        "destination_country": "GB",
                        "last_event_time": "2026-05-06T08:00:00+00:00",
                        "last_fetched_at": "2026-05-06T08:10:00+00:00",
                        "cache_expires_at": "2026-05-06T10:10:00+00:00",
                        "events": [],
                        "raw_response": {},
                    }
                )
                with TestClient(main_module.app) as client:
                    response = client.get(
                        "/internal/api/recent",
                        headers={"x-internal-token": "secret-token"},
                    )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["count"], 1)
                self.assertEqual(payload["shipments"][0]["trackingNumber"], "RJ556381428CN")
            finally:
                object.__setattr__(config_module.settings, "internal_dashboard_token", original_token)
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_parse_tracking_numbers(self) -> None:
        numbers = parse_tracking_numbers("YT2423821266000001 invalid RJ556381428CN YT2423821266000001")
        self.assertEqual(numbers, ["YT2423821266000001", "RJ556381428CN"])

    def test_order_number_validation(self) -> None:
        self.assertTrue(is_valid_order_number("LUK2806"))
        self.assertTrue(is_valid_order_number("#1001"))
        self.assertFalse(is_valid_order_number("2806"))
        self.assertFalse(is_valid_order_number("LUK-2806"))
        self.assertFalse(is_valid_order_number("ABC"))
        self.assertFalse(is_valid_order_number("YT2613500705594269"))

    def test_shopify_admin_order_summary_keeps_images_and_prices(self) -> None:
        order = {
            "id": "gid://shopify/Order/123456789",
            "name": "LUK2781",
            "email": "demo@example.com",
            "processedAt": "2026-05-06T12:33:00Z",
            "displayFulfillmentStatus": "FULFILLED",
            "displayFinancialStatus": "PAID",
            "currentTotalPriceSet": {
                "shopMoney": {"amount": "215.84", "currencyCode": "GBP"}
            },
            "lineItems": {
                "nodes": [
                    {
                        "name": "100% Linen Shawl V-Neck Cap Sleeves Top ELARA",
                        "quantity": 1,
                        "variantTitle": "Rosa / S",
                        "image": {"url": "https://example.com/elara.jpg"},
                        "originalUnitPriceSet": {
                            "shopMoney": {"amount": "57.51", "currencyCode": "GBP"}
                        },
                    }
                ]
            },
        }
        summary = shopify_admin_module._parse_order_summary(order)
        self.assertEqual(summary.order_name, "LUK2781")
        self.assertEqual(summary.total_amount, "215.84")
        self.assertEqual(summary.items[0].variant, "Rosa / S")
        self.assertEqual(summary.items[0].image_url, "https://example.com/elara.jpg")
        self.assertEqual(summary.items[0].unit_price, "57.51 GBP")

    def test_shopify_admin_extracts_tracking_references_from_fulfillments(self) -> None:
        references = shopify_admin_module._extract_tracking_references(
            {
                "fulfillments": [
                    {
                        "tracking_company": "4PX",
                        "tracking_number": "4PX3001999027341CN",
                        "tracking_numbers": ["4PX3001999027341CN", "  "],
                    },
                    {
                        "tracking_company": "YunExpress",
                        "tracking_numbers": ["YT2613500705594269"],
                    },
                ]
            }
        )
        self.assertEqual(
            [item.tracking_number for item in references],
            ["4PX3001999027341CN", "YT2613500705594269"],
        )
        self.assertEqual(references[0].carrier_name, "4PX")

    def test_shopify_admin_extracts_next_page_info_from_link_header(self) -> None:
        self.assertEqual(
            shopify_admin_module._extract_next_page_info(
                '<https://demo.myshopify.com/admin/api/2026-04/orders.json?page_info=abc123&limit=250>; rel="next", '
                '<https://demo.myshopify.com/admin/api/2026-04/orders.json?page_info=zzz999&limit=250>; rel="previous"'
            ),
            "abc123",
        )

    def test_shopify_admin_omits_status_when_following_page_info(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict[str, object], link_header: str | None = None) -> None:
                self._payload = payload
                self.headers = {"Link": link_header} if link_header else {}

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        original_db_path = config_module.settings.database_path
        original_urlopen = shopify_admin_module.urlopen
        urls: list[str] = []
        responses = iter(
            [
                FakeResponse(
                    {"orders": []},
                    '<https://demo.myshopify.com/admin/api/2026-04/orders.json?page_info=abc123&limit=250>; rel="next"',
                ),
                FakeResponse({"orders": []}),
            ]
        )

        def fake_urlopen(request, timeout=0):
            urls.append(request.full_url)
            return next(responses)

        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                shopify_admin_module.urlopen = fake_urlopen
                client = shopify_admin_module.ShopifyAdminClient(access_token="shpat_demo")
                mappings = client.iter_order_tracking_mappings("demo.myshopify.com", max_pages=2)
                self.assertEqual(mappings, [])
                self.assertEqual(len(urls), 2)
                self.assertIn("status=any", urls[0])
                self.assertIn("page_info=abc123", urls[1])
                self.assertNotIn("status=any", urls[1])
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                shopify_admin_module.urlopen = original_urlopen

    def test_shopify_admin_matches_order_identity_with_optional_hash_prefix(self) -> None:
        order = {
            "name": "#LFR1112",
            "email": "nm.clement@orange.fr",
        }
        self.assertTrue(
            shopify_admin_module._matches_order_identity(
                order,
                "LFR1112",
                "nm.clement@orange.fr",
            )
        )
        self.assertTrue(
            shopify_admin_module._matches_order_identity(
                order,
                "#LFR1112",
                "nm.clement@orange.fr",
            )
        )

    def test_normalize_known_main_status(self) -> None:
        self.assertEqual(normalize_status("Delivered", None, None), "delivered")
        self.assertEqual(status_label("delivered"), "Delivered")

    def test_proxy_signature_shape(self) -> None:
        secret = "shpss_test_secret"
        message = "logged_in_customer_id=shop=demo.myshopify.comtimestamp=1700000000"
        signature = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(len(signature), 64)

    def test_sync_tracking_mappings_from_order_webhook_replaces_existing_rows(self) -> None:
        original_db_path = config_module.settings.database_path
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                upsert_order_tracking_number(
                    tracking_number="OLD123456789CN",
                    carrier_code="",
                    shop_domain="demo.myshopify.com",
                    order_name="#1001",
                    source="manual",
                )
                result = webhooks_module.sync_tracking_mappings_from_webhook(
                    "orders/updated",
                    "demo.myshopify.com",
                    {
                        "name": "#1001",
                        "fulfillments": [
                            {
                                "tracking_company": "4PX",
                                "tracking_numbers": [
                                    "4PX3001999027341CN",
                                    "YT2613500705594269",
                                ],
                            }
                        ],
                    },
                )
                self.assertEqual(result["syncedCount"], 2)
                rows = list_order_tracking_numbers_for_order_names(
                    "demo.myshopify.com",
                    ["#1001"],
                )
                self.assertEqual(
                    sorted(row["tracking_number"] for row in rows),
                    ["4PX3001999027341CN", "YT2613500705594269"],
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)

    def test_sync_tracking_mappings_from_fulfillment_webhook_fetches_order_payload(self) -> None:
        original_db_path = config_module.settings.database_path
        original_fetch = webhooks_module.shopify_admin_client.fetch_order_payload
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
                webhooks_module.shopify_admin_client.fetch_order_payload = lambda *_args, **_kwargs: {
                    "name": "LC8152308",
                    "fulfillments": [
                        {
                            "tracking_numbers": [
                                "YT2611001002223456",
                                "YT2611001002216716",
                            ]
                        }
                    ],
                }
                result = webhooks_module.sync_tracking_mappings_from_webhook(
                    "fulfillments/create",
                    "demo.myshopify.com",
                    {"order_id": 123456789},
                )
                self.assertEqual(result["syncedCount"], 2)
                rows = list_order_tracking_numbers_for_order_names(
                    "demo.myshopify.com",
                    ["LC8152308"],
                )
                self.assertEqual(len(rows), 2)
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                webhooks_module.shopify_admin_client.fetch_order_payload = original_fetch

    def test_shopify_webhook_endpoint_verifies_signature_and_syncs_rows(self) -> None:
        original_db_path = config_module.settings.database_path
        original_secret = config_module.settings.shopify_app_secret
        original_allowed = config_module.settings.allowed_shop_domains
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                object.__setattr__(config_module.settings, "shopify_app_secret", "shpss_test_secret")
                object.__setattr__(config_module.settings, "allowed_shop_domains", ("demo.myshopify.com",))
                init_db()
                payload = {
                    "name": "#1002",
                    "fulfillments": [
                        {
                            "tracking_numbers": ["YT2603300702516080"],
                        }
                    ],
                }
                raw = json.dumps(payload).encode("utf-8")
                signature = base64.b64encode(
                    hmac.new(
                        b"shpss_test_secret",
                        raw,
                        hashlib.sha256,
                    ).digest()
                ).decode("utf-8")
                with TestClient(main_module.app) as client:
                    response = client.post(
                        "/api/shopify/webhooks",
                        content=raw,
                        headers={
                            "Content-Type": "application/json",
                            "X-Shopify-Topic": "orders/updated",
                            "X-Shopify-Shop-Domain": "demo.myshopify.com",
                            "X-Shopify-Hmac-Sha256": signature,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    is_store_order_tracking_number(
                        "YT2603300702516080",
                        None,
                        "demo.myshopify.com",
                    )
                )
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                object.__setattr__(config_module.settings, "shopify_app_secret", original_secret)
                object.__setattr__(config_module.settings, "allowed_shop_domains", original_allowed)

    def test_parse_track_info_casts_numeric_carrier(self) -> None:
        parsed = parse_track_info(
            {
                "data": {
                    "accepted": [
                        {
                            "number": "RJ556381428CN",
                            "carrier": 3011,
                            "track": {
                                "z0": 10,
                                "z1": "In transit",
                                "tracking": [
                                    {
                                        "eventTime": 1777539008,
                                        "location": 100,
                                        "description": "Accepted",
                                        "status": 10,
                                    }
                                ],
                            },
                        }
                    ]
                }
            },
            "RJ556381428CN",
        )
        self.assertEqual(parsed["carrier_code"], "3011")
        self.assertEqual(parsed["carrier_name"], "3011")
        self.assertEqual(parsed["provider_status"], "10")
        self.assertEqual(parsed["events"][0]["location"], "100")

    def test_parse_track_info_supports_v2_track_info_payload(self) -> None:
        parsed = parse_track_info(
            {
                "code": 0,
                "data": {
                    "accepted": [
                        {
                            "number": "YT2610601001467359",
                            "carrier": 190008,
                            "track_info": {
                                "shipping_info": {
                                    "shipper_address": {"country": "CN"},
                                    "recipient_address": {"country": "US"},
                                },
                                "latest_status": {
                                    "status": "Delivered",
                                    "sub_status": "Delivered_Other",
                                    "sub_status_descr": None,
                                },
                                "latest_event": {
                                    "time_iso": "2026-04-22T13:27:08-04:00",
                                    "time_utc": "2026-04-22T17:27:08Z",
                                    "description": "Delivered, Door/Yard",
                                    "location": "Palm Beach Gardens",
                                    "stage": "Delivered",
                                },
                                "tracking": {
                                    "providers": [
                                        {
                                            "provider": {
                                                "key": 190008,
                                                "name": "YunExpress",
                                                "alias": "YunExpress 云途物流",
                                            },
                                            "events": [
                                                {
                                                    "time_iso": "2026-04-22T13:27:08-04:00",
                                                    "time_utc": "2026-04-22T17:27:08Z",
                                                    "description": "Delivered, Door/Yard",
                                                    "location": "Palm Beach Gardens",
                                                    "stage": "Delivered",
                                                },
                                                {
                                                    "time_iso": "2026-04-22T08:50:08-04:00",
                                                    "time_utc": "2026-04-22T12:50:08Z",
                                                    "description": "Out for Delivery",
                                                    "location": "Palm Beach Gardens",
                                                    "stage": "OutForDelivery",
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                        }
                    ]
                },
            },
            "YT2610601001467359",
        )
        self.assertEqual(parsed["carrier_code"], "190008")
        self.assertEqual(parsed["carrier_name"], "YunExpress")
        self.assertEqual(parsed["normalized_status"], "delivered")
        self.assertEqual(parsed["provider_status"], "Delivered")
        self.assertEqual(parsed["origin_country"], "CN")
        self.assertEqual(parsed["destination_country"], "US")
        self.assertEqual(parsed["last_event_time"], "2026-04-22T17:27:08Z")
        self.assertEqual(len(parsed["events"]), 2)
        self.assertEqual(parsed["events"][0]["providerStatus"], "Delivered")
        self.assertEqual(parsed["events"][1]["providerStatus"], "OutForDelivery")

    def test_parse_track_info_handles_string_latest_event(self) -> None:
        parsed = parse_track_info(
            {
                "data": {
                    "accepted": [
                        {
                            "number": "4PX3002754801725CN",
                            "carrier": "190094",
                            "track": {
                                "z0": "NotFound",
                                "z1": "No tracking updates yet",
                                "latest_event": "No tracking updates yet",
                                "origin_info": {"item_pre_advice": "CN"},
                                "destination_info": {"item_dest_country": "GB"},
                                "tracking": [],
                            },
                        }
                    ]
                }
            },
            "4PX3002754801725CN",
        )
        self.assertEqual(parsed["carrier_code"], "190094")
        self.assertEqual(parsed["provider_status"], "NotFound")
        self.assertEqual(parsed["status_text"], "No tracking updates yet")
        self.assertEqual(parsed["destination_country"], "GB")

    def test_import_tool_header_helpers_support_sampled_order_csv(self) -> None:
        fieldnames = ["???", "??", "???", "????"]
        self.assertEqual(_normalize_header("Tracking Number"), "trackingnumber")
        self.assertEqual(_pick_column(fieldnames, {"tracking_number"}, fallback_index=2), "???")
        self.assertEqual(_pick_column(fieldnames, {"order_number"}, fallback_index=0), "???")
        self.assertTrue(_should_use_positional_fallback(fieldnames))
        self.assertFalse(_should_use_positional_fallback(["order_number", "email", "tracking_number", "created_at"]))
        self.assertEqual(
            list(_split_tracking_values("YT2603300702516080; YT2610501001696199\r\n4PX3001999027341CN")),
            ["YT2603300702516080", "YT2610501001696199", "4PX3001999027341CN"],
        )

    def test_summarize_daily_usage_and_report(self) -> None:
        original_db_path = config_module.settings.database_path
        original_url = config_module.settings.alert_webhook_url
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                object.__setattr__(config_module.settings, "alert_webhook_url", "")
                init_db()
                now = datetime.now(timezone.utc)
                upsert_tracking_record(
                    {
                        "tracking_number": "RJ556381428CN",
                        "carrier_code": "3011",
                        "carrier_name": "YunExpress",
                        "shop_domain": "demo.myshopify.com",
                        "is_registered": True,
                        "normalized_status": "in_transit",
                        "status_text": "In transit",
                        "provider_status": "InTransit",
                        "provider_status_description": "Parcel moving",
                        "origin_country": "CN",
                        "destination_country": "GB",
                        "last_event_time": "2026-05-06T08:00:00+00:00",
                        "last_fetched_at": now.isoformat(),
                        "cache_expires_at": now.isoformat(),
                        "events": [],
                        "raw_response": {},
                    }
                )
                services_module.log_event("tracking_query_success", tracking_number="RJ556381428CN")
                services_module.log_event("tracking_not_store_order", level="warning", tracking_number="BAD123456")
                services_module.log_event("ip_rate_limited", level="warning", client_ip="127.0.0.1")

                summary = summarize_daily_usage()
                self.assertGreaterEqual(summary["firstSeenTrackingCount"], 1)
                self.assertGreaterEqual(summary["refreshedTrackingCount"], 1)
                self.assertEqual(summary["successfulQueryCount"], 1)
                self.assertEqual(summary["notStoreOrderCount"], 1)
                self.assertEqual(summary["rateLimitedCount"], 1)

                report = send_daily_usage_report()
                self.assertIn("notes", report)
                self.assertEqual(report["successfulQueryCount"], 1)
            finally:
                object.__setattr__(config_module.settings, "database_path", original_db_path)
                object.__setattr__(config_module.settings, "alert_webhook_url", original_url)


if __name__ == "__main__":
    unittest.main()
