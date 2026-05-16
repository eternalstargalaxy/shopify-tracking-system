from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.db import init_db, replace_order_tracking_numbers_for_order_name
from backend.app.shopify_admin import ShopifyAdminClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill historical Shopify order-to-tracking mappings into order_tracking_numbers."
    )
    parser.add_argument("--shop-domain", required=True, help="Shopify shop domain, e.g. 2vnpww-33.myshopify.com")
    parser.add_argument(
        "--updated-after",
        default=None,
        help="Optional updated_at_min timestamp (ISO 8601) to limit the historical backfill window.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional maximum number of Shopify REST pages to process.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help="Orders per page (max 250).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect what would be synced without writing to the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()

    client = ShopifyAdminClient()
    mappings = client.iter_order_tracking_mappings(
        args.shop_domain,
        updated_at_min=args.updated_after,
        limit=args.limit,
        max_pages=args.max_pages,
    )

    synced_orders = 0
    synced_trackings = 0
    skipped_orders = 0

    for mapping in mappings:
        tracking_numbers = [item.tracking_number for item in mapping.tracking_numbers]
        if not tracking_numbers:
            skipped_orders += 1
            continue
        if not args.dry_run:
            replace_order_tracking_numbers_for_order_name(
                args.shop_domain,
                mapping.order_name,
                tracking_numbers,
                source="shopify_backfill",
            )
        synced_orders += 1
        synced_trackings += len(tracking_numbers)

    summary = {
        "shopDomain": args.shop_domain,
        "updatedAfter": args.updated_after,
        "dryRun": args.dry_run,
        "syncedOrders": synced_orders,
        "syncedTrackings": synced_trackings,
        "skippedOrders": skipped_orders,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
