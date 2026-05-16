from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.db import init_db, upsert_order_tracking_number


ORDER_FIELD_ALIASES = {
    "ordername",
    "orderno",
    "ordernumber",
    "order_no",
    "order_number",
    "name",
}

TRACKING_FIELD_ALIASES = {
    "trackingnumber",
    "trackingno",
    "tracking",
    "tracking_number",
    "tracking_no",
    "waybill",
    "waybillnumber",
}

CARRIER_FIELD_ALIASES = {
    "carriercode",
    "carrier_code",
    "carrier",
}

SHOP_FIELD_ALIASES = {
    "shopdomain",
    "shop",
    "shop_domain",
}


def _normalize_header(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _pick_column(fieldnames: list[str], aliases: set[str], fallback_index: int | None = None) -> str | None:
    normalized = {_normalize_header(name): name for name in fieldnames}
    for alias in aliases:
        match = normalized.get(_normalize_header(alias))
        if match:
            return match
    if fallback_index is not None and 0 <= fallback_index < len(fieldnames):
        return fieldnames[fallback_index]
    return None


def _should_use_positional_fallback(fieldnames: list[str]) -> bool:
    normalized = [_normalize_header(name) for name in fieldnames]
    if not normalized:
        return True
    non_empty = [name for name in normalized if name]
    if len(non_empty) != len(set(non_empty)):
        return True
    recognized = (
        ORDER_FIELD_ALIASES
        | TRACKING_FIELD_ALIASES
        | CARRIER_FIELD_ALIASES
        | SHOP_FIELD_ALIASES
    )
    return not any(name in recognized for name in normalized)


def _split_tracking_values(value: str) -> Iterable[str]:
    for part in value.replace("\r", "\n").replace(";", "\n").replace(",", "\n").split("\n"):
        candidate = part.strip().upper()
        if candidate:
            yield candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Import store order tracking numbers from CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--shop-domain", default="")
    parser.add_argument("--source", default="csv")
    args = parser.parse_args()

    init_db()
    with args.csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        fieldnames = next(reader, [])
        use_positional_fallback = _should_use_positional_fallback(fieldnames)
        order_field = _pick_column(fieldnames, ORDER_FIELD_ALIASES, fallback_index=0)
        tracking_field = _pick_column(fieldnames, TRACKING_FIELD_ALIASES, fallback_index=2)
        carrier_field = _pick_column(fieldnames, CARRIER_FIELD_ALIASES)
        shop_field = _pick_column(fieldnames, SHOP_FIELD_ALIASES)

        for raw_row in reader:
            if not raw_row:
                continue
            row = {fieldnames[index]: value for index, value in enumerate(raw_row[: len(fieldnames)])}
            if use_positional_fallback:
                tracking_raw = (raw_row[2] if len(raw_row) > 2 else "").strip()
                order_name = (raw_row[0] if len(raw_row) > 0 else "").strip()
                carrier_code = ""
                shop_domain = args.shop_domain.strip()
            else:
                tracking_raw = (row.get(tracking_field or "") or "").strip()
                order_name = (row.get(order_field or "") or "").strip()
                carrier_code = (row.get(carrier_field or "") or "").strip()
                shop_domain = (row.get(shop_field or "") or args.shop_domain).strip()
            if not tracking_raw:
                continue
            for tracking_number in _split_tracking_values(tracking_raw):
                upsert_order_tracking_number(
                    tracking_number=tracking_number,
                    carrier_code=carrier_code,
                    shop_domain=shop_domain,
                    order_name=order_name,
                    source=args.source,
                )


if __name__ == "__main__":
    main()
