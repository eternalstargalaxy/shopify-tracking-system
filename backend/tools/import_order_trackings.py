from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.db import init_db, upsert_order_tracking_number


def main() -> None:
    parser = argparse.ArgumentParser(description="Import store order tracking numbers from CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--shop-domain", default="")
    parser.add_argument("--source", default="csv")
    args = parser.parse_args()

    init_db()
    with args.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            tracking_number = (row.get("tracking_number") or "").strip().upper()
            if not tracking_number:
                continue
            upsert_order_tracking_number(
                tracking_number=tracking_number,
                carrier_code=(row.get("carrier_code") or "").strip(),
                shop_domain=(row.get("shop_domain") or args.shop_domain).strip(),
                order_name=(row.get("order_name") or "").strip(),
                source=args.source,
            )


if __name__ == "__main__":
    main()
