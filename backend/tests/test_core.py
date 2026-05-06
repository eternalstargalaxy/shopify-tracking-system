from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app import config as config_module
from backend.app import main as main_module
from backend.app.db import init_db, upsert_tracking_record
from backend.app.normalization import normalize_status, status_label
from backend.app.seventeen_track import parse_track_info
from backend.app.services import parse_tracking_numbers


class CoreTests(unittest.TestCase):
    def test_internal_api_requires_token(self) -> None:
        original_token = config_module.settings.internal_dashboard_token
        original_mock = config_module.settings.mock_when_api_key_missing
        original_db_path = config_module.settings.database_path
        original_client_key = main_module.client.api_key
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "internal_dashboard_token", "secret-token")
                object.__setattr__(config_module.settings, "mock_when_api_key_missing", True)
                object.__setattr__(config_module.settings, "database_path", f"{temp_dir}/test.sqlite3")
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
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                object.__setattr__(config_module.settings, "internal_dashboard_token", "secret-token")
                object.__setattr__(config_module.settings, "database_path", f"{temp_dir}/test.sqlite3")
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


if __name__ == "__main__":
    unittest.main()
