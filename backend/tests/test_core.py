from __future__ import annotations

import hashlib
import hmac
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import config as config_module
from backend.app import main as main_module
from backend.app.db import (
    count_recent_system_events,
    fetch_tracking_record,
    init_db,
    is_store_order_tracking_number,
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
from backend.app.schemas import OrderSummary, OrderSummaryItem
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
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                init_db()
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
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
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
                services_module.storefront_client.lookup_by_order = original_lookup_by_order
                services_module.shopify_admin_client.access_token = original_admin_token

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
            "name": "LUK2781",
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

    def test_normalize_known_main_status(self) -> None:
        self.assertEqual(normalize_status("Delivered", None, None), "delivered")
        self.assertEqual(status_label("delivered"), "Delivered")

    def test_proxy_signature_shape(self) -> None:
        secret = "shpss_test_secret"
        message = "logged_in_customer_id=shop=demo.myshopify.comtimestamp=1700000000"
        signature = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(len(signature), 64)

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

    def test_summarize_daily_usage_and_report(self) -> None:
        original_db_path = config_module.settings.database_path
        original_url = config_module.settings.alert_webhook_url
        with workspace_temp_dir() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "database_path", str(temp_dir / "test.sqlite3"))
                object.__setattr__(config_module.settings, "alert_webhook_url", "")
                init_db()
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
                        "last_fetched_at": "2026-05-15T08:10:00+00:00",
                        "cache_expires_at": "2026-05-15T10:10:00+00:00",
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
