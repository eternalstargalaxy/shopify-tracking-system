from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings

NORMALIZED_STATUS_VALUES = (
    "info_received",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "failed_attempt",
    "not_found",
    "expired",
    "unknown",
)

LAST_ERROR_CODE_VALUES = (
    "empty_tracking_number",
    "invalid_tracking_number",
    "invalid_shopify_signature",
    "expired_shopify_signature",
    "shop_not_allowed",
    "not_store_order",
    "rate_limited",
    "tracking_refresh_limited",
    "upstream_register_failed",
    "upstream_query_failed",
    "query_error",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS tracking_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_number TEXT NOT NULL,
                carrier_code TEXT NOT NULL DEFAULT '',
                carrier_name TEXT,
                shop_domain TEXT,
                is_registered INTEGER NOT NULL DEFAULT 0,
                normalized_status TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (normalized_status IN {NORMALIZED_STATUS_VALUES}),
                status_text TEXT,
                provider_status TEXT,
                provider_status_description TEXT,
                origin_country TEXT,
                destination_country TEXT,
                last_event_time TEXT,
                last_fetched_at TEXT,
                cache_expires_at TEXT,
                fetch_count INTEGER NOT NULL DEFAULT 0,
                events_json TEXT NOT NULL DEFAULT '[]',
                raw_response TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (tracking_number, carrier_code)
            );

            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket_key TEXT NOT NULL,
                bucket_type TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (bucket_key, bucket_type, window_start)
            );

            CREATE TABLE IF NOT EXISTS order_tracking_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_domain TEXT NOT NULL DEFAULT '',
                order_name TEXT,
                tracking_number TEXT NOT NULL,
                carrier_code TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (shop_domain, tracking_number, carrier_code)
            );

            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT,
                context_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tracking_records)").fetchall()
    }
    additions = {
        "provider_status": "ALTER TABLE tracking_records ADD COLUMN provider_status TEXT",
        "provider_status_description": (
            "ALTER TABLE tracking_records ADD COLUMN provider_status_description TEXT"
        ),
    }
    for column, statement in additions.items():
        if column not in existing:
            conn.execute(statement)


def fetch_tracking_record(tracking_number: str, carrier_code: str | None) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        if carrier_code is not None:
            carrier_key = carrier_code or ""
            return conn.execute(
                """
                SELECT *
                FROM tracking_records
                WHERE tracking_number = ? AND carrier_code = ?
                """,
                (tracking_number, carrier_key),
            ).fetchone()
        return conn.execute(
            """
            SELECT *
            FROM tracking_records
            WHERE tracking_number = ?
            ORDER BY
                CASE WHEN carrier_code = '' THEN 1 ELSE 0 END,
                COALESCE(last_fetched_at, updated_at, created_at) DESC
            LIMIT 1
            """,
            (tracking_number,),
        ).fetchone()
    finally:
        conn.close()


def upsert_tracking_record(payload: dict[str, Any]) -> None:
    conn = get_connection()
    now = _now_iso()
    carrier_key = payload.get("carrier_code") or ""
    try:
        existing = conn.execute(
            """
            SELECT id, fetch_count, created_at
            FROM tracking_records
            WHERE tracking_number = ? AND carrier_code = ?
            """,
            (payload["tracking_number"], carrier_key),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        fetch_count = int(existing["fetch_count"]) + 1 if existing else 1
        serialized_events = json.dumps(payload.get("events", []), ensure_ascii=True)
        serialized_raw = json.dumps(payload.get("raw_response", {}), ensure_ascii=True)
        conn.execute(
            """
            INSERT INTO tracking_records (
                tracking_number,
                carrier_code,
                carrier_name,
                shop_domain,
                is_registered,
                normalized_status,
                status_text,
                provider_status,
                provider_status_description,
                origin_country,
                destination_country,
                last_event_time,
                last_fetched_at,
                cache_expires_at,
                fetch_count,
                events_json,
                raw_response,
                last_error_code,
                last_error_message,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tracking_number, carrier_code) DO UPDATE SET
                carrier_name = excluded.carrier_name,
                shop_domain = excluded.shop_domain,
                is_registered = excluded.is_registered,
                normalized_status = excluded.normalized_status,
                status_text = excluded.status_text,
                provider_status = excluded.provider_status,
                provider_status_description = excluded.provider_status_description,
                origin_country = excluded.origin_country,
                destination_country = excluded.destination_country,
                last_event_time = excluded.last_event_time,
                last_fetched_at = excluded.last_fetched_at,
                cache_expires_at = excluded.cache_expires_at,
                fetch_count = excluded.fetch_count,
                events_json = excluded.events_json,
                raw_response = excluded.raw_response,
                last_error_code = excluded.last_error_code,
                last_error_message = excluded.last_error_message,
                updated_at = excluded.updated_at
            """,
            (
                payload["tracking_number"],
                carrier_key,
                payload.get("carrier_name"),
                payload.get("shop_domain"),
                1 if payload.get("is_registered") else 0,
                payload["normalized_status"],
                payload["status_text"],
                payload.get("provider_status"),
                payload.get("provider_status_description"),
                payload.get("origin_country"),
                payload.get("destination_country"),
                payload.get("last_event_time"),
                payload["last_fetched_at"],
                payload["cache_expires_at"],
                fetch_count,
                serialized_events,
                serialized_raw,
                payload.get("last_error_code"),
                payload.get("last_error_message"),
                created_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def is_store_order_tracking_number(
    tracking_number: str,
    carrier_code: str | None,
    shop_domain: str | None,
) -> bool:
    conn = get_connection()
    try:
        if carrier_code is None:
            row = conn.execute(
                """
                SELECT id
                FROM order_tracking_numbers
                WHERE tracking_number = ?
                  AND (shop_domain = ? OR shop_domain IS NULL OR shop_domain = '')
                LIMIT 1
                """,
                (tracking_number, shop_domain or ""),
            ).fetchone()
        else:
            carrier_key = carrier_code or ""
            row = conn.execute(
                """
                SELECT id
                FROM order_tracking_numbers
                WHERE tracking_number = ?
                  AND carrier_code IN (?, '')
                  AND (shop_domain = ? OR shop_domain IS NULL OR shop_domain = '')
                LIMIT 1
                """,
                (tracking_number, carrier_key, shop_domain or ""),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def fetch_order_tracking_match(
    tracking_number: str,
    carrier_code: str | None,
    shop_domain: str | None,
) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        if carrier_code is None:
            return conn.execute(
                """
                SELECT *
                FROM order_tracking_numbers
                WHERE tracking_number = ?
                  AND (shop_domain = ? OR shop_domain IS NULL OR shop_domain = '')
                ORDER BY
                  CASE WHEN shop_domain = ? THEN 0 ELSE 1 END,
                  CASE WHEN carrier_code = '' THEN 1 ELSE 0 END,
                  updated_at DESC
                LIMIT 1
                """,
                (tracking_number, shop_domain or "", shop_domain or ""),
            ).fetchone()

        carrier_key = carrier_code or ""
        return conn.execute(
            """
            SELECT *
            FROM order_tracking_numbers
            WHERE tracking_number = ?
              AND carrier_code IN (?, '')
              AND (shop_domain = ? OR shop_domain IS NULL OR shop_domain = '')
            ORDER BY
              CASE WHEN carrier_code = ? THEN 0 ELSE 1 END,
              CASE WHEN shop_domain = ? THEN 0 ELSE 1 END,
              updated_at DESC
            LIMIT 1
            """,
            (tracking_number, carrier_key, shop_domain or "", carrier_key, shop_domain or ""),
        ).fetchone()
    finally:
        conn.close()


def list_order_tracking_numbers_for_order_names(
    shop_domain: str | None,
    order_names: list[str],
) -> list[sqlite3.Row]:
    cleaned = [name.strip() for name in order_names if name and name.strip()]
    if not cleaned:
        return []
    placeholders = ", ".join("?" for _ in cleaned)
    conn = get_connection()
    try:
        return conn.execute(
            f"""
            SELECT *
            FROM order_tracking_numbers
            WHERE order_name IN ({placeholders})
              AND (shop_domain = ? OR shop_domain IS NULL OR shop_domain = '')
            ORDER BY
              CASE WHEN shop_domain = ? THEN 0 ELSE 1 END,
              updated_at DESC
            """,
            (*cleaned, shop_domain or "", shop_domain or ""),
        ).fetchall()
    finally:
        conn.close()


def upsert_order_tracking_number(
    tracking_number: str,
    carrier_code: str | None = None,
    shop_domain: str | None = None,
    order_name: str | None = None,
    source: str = "manual",
) -> None:
    carrier_key = carrier_code or ""
    shop_key = shop_domain or ""
    conn = get_connection()
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO order_tracking_numbers (
                shop_domain,
                order_name,
                tracking_number,
                carrier_code,
                source,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(shop_domain, tracking_number, carrier_code) DO UPDATE SET
                order_name = excluded.order_name,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (shop_key, order_name, tracking_number, carrier_key, source, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_tracking_records(limit: int = 20) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM tracking_records
            ORDER BY COALESCE(last_fetched_at, updated_at, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(rows)
    finally:
        conn.close()


def consume_rate_limit(bucket_type: str, bucket_key: str, window_start: int) -> int:
    conn = get_connection()
    now = _now_iso()
    try:
        row = conn.execute(
            """
            SELECT id, count
            FROM rate_limit_hits
            WHERE bucket_type = ? AND bucket_key = ? AND window_start = ?
            """,
            (bucket_type, bucket_key, window_start),
        ).fetchone()
        if row:
            new_count = int(row["count"]) + 1
            conn.execute(
                """
                UPDATE rate_limit_hits
                SET count = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_count, now, row["id"]),
            )
        else:
            new_count = 1
            conn.execute(
                """
                INSERT INTO rate_limit_hits (
                    bucket_key, bucket_type, window_start, count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bucket_key, bucket_type, window_start, new_count, now, now),
            )
        conn.commit()
        return new_count
    finally:
        conn.close()


def replace_order_tracking_numbers_for_order_name(
    shop_domain: str | None,
    order_name: str | None,
    tracking_numbers: list[str],
    *,
    source: str = "shopify_webhook",
) -> int:
    shop_key = shop_domain or ""
    normalized_order_name = (order_name or "").strip()
    if not normalized_order_name:
        return 0

    deduped_tracking_numbers: list[str] = []
    seen: set[str] = set()
    for value in tracking_numbers:
        normalized = str(value or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped_tracking_numbers.append(normalized)

    conn = get_connection()
    now = _now_iso()
    try:
        conn.execute(
            """
            DELETE FROM order_tracking_numbers
            WHERE shop_domain = ? AND order_name = ?
            """,
            (shop_key, normalized_order_name),
        )
        for tracking_number in deduped_tracking_numbers:
            conn.execute(
                """
                INSERT INTO order_tracking_numbers (
                    shop_domain,
                    order_name,
                    tracking_number,
                    carrier_code,
                    source,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, '', ?, ?, ?)
                """,
                (
                    shop_key,
                    normalized_order_name,
                    tracking_number,
                    source,
                    now,
                    now,
                ),
            )
        conn.commit()
        return len(deduped_tracking_numbers)
    finally:
        conn.close()


def insert_system_event(
    event: str,
    level: str,
    message: str | None,
    context: dict[str, Any] | None = None,
) -> None:
    conn = get_connection()
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO system_events (event, level, message, context_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event,
                level,
                message,
                json.dumps(context or {}, ensure_ascii=True),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def summarize_system_events(limit: int = 20) -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT event, level, message, context_json, created_at
            FROM system_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        count_24h = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM system_events
            WHERE created_at >= datetime('now', '-1 day')
            """
        ).fetchone()["count"]
        error_count_24h = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM system_events
            WHERE created_at >= datetime('now', '-1 day')
              AND level = 'error'
            """
        ).fetchone()["count"]
        warning_count_24h = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM system_events
            WHERE created_at >= datetime('now', '-1 day')
              AND level = 'warning'
            """
        ).fetchone()["count"]

        recent_events = []
        for row in rows:
            try:
                context = json.loads(row["context_json"] or "{}")
            except json.JSONDecodeError:
                context = {}
            recent_events.append(
                {
                    "event": row["event"],
                    "level": row["level"],
                    "message": row["message"],
                    "createdAt": row["created_at"],
                    "context": context,
                }
            )

        return {
            "count24h": int(count_24h),
            "errorCount24h": int(error_count_24h),
            "warningCount24h": int(warning_count_24h),
            "recentEvents": recent_events,
        }
    finally:
        conn.close()


def count_recent_system_events(events: str | list[str] | tuple[str, ...], within_seconds: int) -> int:
    event_names = [events] if isinstance(events, str) else list(events)
    if not event_names:
        return 0

    cutoff = (datetime.now(timezone.utc).timestamp() - max(within_seconds, 1))
    cutoff_text = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in event_names)
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM system_events
            WHERE event IN ({placeholders})
              AND created_at >= ?
            """,
            (*event_names, cutoff_text),
        ).fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def summarize_daily_usage(day: str | None = None) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(DISTINCT CASE WHEN date(created_at) = date(COALESCE(?, 'now')) THEN tracking_number END) AS first_seen_tracking_count,
              COUNT(DISTINCT CASE WHEN date(last_fetched_at) = date(COALESCE(?, 'now')) THEN tracking_number END) AS refreshed_tracking_count
            FROM tracking_records
            """,
            (day, day),
        ).fetchone()

        def _event_count(event_names: tuple[str, ...]) -> int:
            placeholders = ",".join("?" for _ in event_names)
            query = f"""
                SELECT COUNT(*) AS count
                FROM system_events
                WHERE event IN ({placeholders})
                  AND date(created_at) = date(COALESCE(?, 'now'))
            """
            result = conn.execute(query, (*event_names, day)).fetchone()
            return int(result["count"]) if result else 0

        summary_day = conn.execute(
            "SELECT date(COALESCE(?, 'now')) AS day",
            (day,),
        ).fetchone()["day"]

        return {
            "date": summary_day,
            "firstSeenTrackingCount": int(row["first_seen_tracking_count"]) if row else 0,
            "refreshedTrackingCount": int(row["refreshed_tracking_count"]) if row else 0,
            "successfulQueryCount": _event_count(("tracking_query_success",)),
            "queryErrorCount": _event_count(("tracking_query_error", "order_lookup_failed", "request_exception", "request_5xx")),
            "notStoreOrderCount": _event_count(("tracking_not_store_order", "order_lookup_not_found")),
            "rateLimitedCount": _event_count(("ip_rate_limited", "ip_daily_rate_limited", "tracking_refresh_limited")),
        }
    finally:
        conn.close()
