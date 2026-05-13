from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "parcelpilot-tracking")
    database_path: str = os.getenv(
        "DATABASE_PATH",
        str(ROOT_DIR / "backend" / "data" / "tracking.sqlite3"),
    )
    shopify_app_secret: str = os.getenv("SHOPIFY_APP_SECRET", "")
    shopify_client_id: str = os.getenv("SHOPIFY_CLIENT_ID", "")
    shopify_client_secret: str = os.getenv("SHOPIFY_CLIENT_SECRET", "")
    shopify_proxy_prefix: str = os.getenv("SHOPIFY_PROXY_PREFIX", "/apps/track/api/track")
    shopify_store_slug: str = os.getenv("SHOPIFY_STORE_SLUG", "")
    shopify_storefront_url: str = os.getenv("SHOPIFY_STOREFRONT_URL", "")
    shopify_admin_access_token: str = os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "")
    shopify_admin_api_version: str = os.getenv("SHOPIFY_ADMIN_API_VERSION", "2026-04")
    allowed_shop_domains: tuple[str, ...] = tuple(
        part.strip()
        for part in os.getenv("ALLOWED_SHOP_DOMAINS", "").split(",")
        if part.strip()
    )
    bypass_proxy_signature: bool = os.getenv("BYPASS_PROXY_SIGNATURE", "false").lower() == "true"
    seventeen_track_api_key: str = os.getenv("SEVENTEEN_TRACK_API_KEY", "")
    seventeen_track_base_url: str = os.getenv(
        "SEVENTEEN_TRACK_BASE_URL",
        "https://api.17track.net/track/v2",
    )
    seventeen_track_shopify_url: str = os.getenv(
        "SEVENTEEN_TRACK_SHOPIFY_URL",
        "https://shopify.17track.net/trackcenterapi/call",
    )
    seventeen_track_shopify_tracking_url: str = os.getenv(
        "SEVENTEEN_TRACK_SHOPIFY_TRACKING_URL",
        "https://shopify-t.17track.net/track/shopify",
    )
    require_order_tracking_match: bool = (
        os.getenv("REQUIRE_ORDER_TRACKING_MATCH", "false").lower() == "true"
    )
    ip_limit_per_minute: int = int(os.getenv("IP_LIMIT_PER_MINUTE", "30"))
    ip_limit_per_day: int = int(os.getenv("IP_LIMIT_PER_DAY", "300"))
    tracking_refresh_window_seconds: int = int(
        os.getenv("TRACKING_REFRESH_WINDOW_SECONDS", "300")
    )
    tracking_refresh_limit: int = int(os.getenv("TRACKING_REFRESH_LIMIT", "3"))
    cache_ttl_active_minutes: int = int(os.getenv("CACHE_TTL_ACTIVE_MINUTES", "120"))
    cache_ttl_delivered_hours: int = int(os.getenv("CACHE_TTL_DELIVERED_HOURS", "72"))
    cache_ttl_not_found_hours: int = int(os.getenv("CACHE_TTL_NOT_FOUND_HOURS", "6"))
    cache_ttl_exception_minutes: int = int(os.getenv("CACHE_TTL_EXCEPTION_MINUTES", "60"))
    hmac_max_age_seconds: int = int(os.getenv("HMAC_MAX_AGE_SECONDS", "300"))
    mock_when_api_key_missing: bool = os.getenv("MOCK_WHEN_API_KEY_MISSING", "true").lower() == "true"
    internal_dashboard_token: str = os.getenv("INTERNAL_DASHBOARD_TOKEN", "")


settings = Settings()
