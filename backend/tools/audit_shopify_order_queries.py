from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.db import init_db
from backend.app.services import query_order_tracking
from backend.app.seventeen_track import SeventeenTrackClient
from backend.app.shopify_admin import ShopifyAdminClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit whether Shopify order numbers can return tracking results."
    )
    parser.add_argument("--shop-domain", required=True, help="Shopify shop domain, e.g. 2vnpww-33.myshopify.com")
    parser.add_argument(
        "--updated-after",
        default=None,
        help="Optional updated_at_min timestamp (ISO 8601) to limit the audit window.",
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
        "--mode",
        choices=("lookup", "full"),
        default="lookup",
        help=(
            "lookup = only verify order-number resolution through Shopify/local mappings; "
            "full = run full order query path, which may call 17TRACK."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()

    admin_client = ShopifyAdminClient()
    order_records = admin_client.iter_orders(
        args.shop_domain,
        updated_at_min=args.updated_after,
        limit=args.limit,
        max_pages=args.max_pages,
    )

    total = len(order_records)
    ok_count = 0
    pending_count = 0
    failures: list[dict[str, object]] = []

    seventeen_track_client = SeventeenTrackClient() if args.mode == "full" else None

    for record in order_records:
        if args.mode == "lookup":
            lookup = admin_client.lookup_order_by_name(args.shop_domain, record.order_name)
            if lookup and (lookup.shipment_pending or lookup.tracking_numbers):
                ok_count += 1
                if lookup.shipment_pending:
                    pending_count += 1
                continue
            failures.append(
                {
                    "orderName": record.order_name,
                    "fulfillmentStatus": record.fulfillment_status,
                    "trackingCount": len(record.tracking_numbers),
                    "reason": "lookup_missed",
                }
            )
            continue

        shipments, errors = query_order_tracking(
            seventeen_track_client,  # type: ignore[arg-type]
            record.order_name,
            None,
            args.shop_domain,
        )
        if shipments:
            ok_count += 1
            if all((shipment.status_text or "").lower() == "not shipped yet" for shipment in shipments):
                pending_count += 1
            continue
        failures.append(
            {
                "orderName": record.order_name,
                "fulfillmentStatus": record.fulfillment_status,
                "trackingCount": len(record.tracking_numbers),
                "reason": errors[0].code if errors else "no_result",
                "message": errors[0].message if errors else "No shipments returned.",
            }
        )

    summary = {
        "shopDomain": args.shop_domain,
        "mode": args.mode,
        "updatedAfter": args.updated_after,
        "totalOrders": total,
        "okCount": ok_count,
        "pendingCount": pending_count,
        "failureCount": len(failures),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
