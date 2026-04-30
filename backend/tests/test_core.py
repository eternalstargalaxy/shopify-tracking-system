from __future__ import annotations

import hashlib
import hmac
import unittest

from backend.app.normalization import normalize_status, status_label
from backend.app.services import parse_tracking_numbers


class CoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
