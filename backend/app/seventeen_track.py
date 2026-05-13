from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from .config import settings
from .normalization import normalize_status


def _optional_text(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


class SeventeenTrackClient:
    def __init__(self) -> None:
        self.base_url = settings.seventeen_track_base_url.rstrip("/")
        self.api_key = settings.seventeen_track_api_key

    @property
    def mock_mode(self) -> bool:
        return not self.api_key and settings.mock_when_api_key_missing

    def register(self, tracking_number: str, carrier_code: str | None) -> dict:
        if self.mock_mode:
            return {"accepted": [{"number": tracking_number, "carrier": carrier_code}]}
        payload = [{"number": tracking_number, "carrier": carrier_code}]
        return self._post("/register", payload)

    def get_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
        if self.mock_mode:
            return self._mock_track_info(tracking_number, carrier_code)
        payload = [{"number": tracking_number, "carrier": carrier_code}]
        return self._post("/gettrackinfo", payload)

    def _post(self, path: str, payload: list[dict]) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=f"{self.base_url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "17token": self.api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"17TRACK HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise HTTPException(status_code=502, detail="17TRACK network error.") from exc

    def _mock_track_info(self, tracking_number: str, carrier_code: str | None) -> dict:
        status_cycle = [
            ("InTransit", "Package arrived at destination sorting center"),
            ("OutForDelivery", "Parcel is with the local delivery partner"),
            ("Delivered", "Delivered at front desk"),
            ("NotFound", "No tracking updates yet"),
            ("Exception", "Address verification required"),
        ]
        selected = status_cycle[sum(map(ord, tracking_number)) % len(status_cycle)]
        main_status, latest_text = selected
        now = datetime.now(timezone.utc)
        events = [
            {
                "eventTime": (now - timedelta(hours=2)).isoformat(),
                "location": "Los Angeles, US",
                "description": latest_text,
                "status": main_status,
            },
            {
                "eventTime": (now - timedelta(days=1)).isoformat(),
                "location": "Hong Kong, CN",
                "description": "Flight landed and transferred",
                "status": "InTransit",
            },
            {
                "eventTime": (now - timedelta(days=2)).isoformat(),
                "location": "Shenzhen, CN",
                "description": "Carrier accepted shipment",
                "status": "InfoReceived",
            },
        ]
        return {
            "data": {
                "accepted": [
                    {
                        "number": tracking_number,
                        "carrier": carrier_code or "auto",
                        "track": {
                            "z0": main_status,
                            "z1": latest_text,
                            "latest_event": latest_text,
                            "origin_info": {"item_pre_advice": "CN"},
                            "destination_info": {"item_dest_country": "US"},
                            "tracking": events,
                        },
                    }
                ]
            }
        }


def parse_track_info(raw_response: dict, tracking_number: str) -> dict:
    shipments = raw_response.get("shipments") or []
    if shipments:
        item = shipments[0] if shipments else {}
        track = item.get("shipment") or {}
    else:
        accepted = (
            raw_response.get("data", {}).get("accepted")
            or raw_response.get("accepted")
            or raw_response.get("data")
            or []
        )
        if isinstance(accepted, dict):
            accepted = [accepted]
        item = accepted[0] if accepted else {}
        track = item.get("track_info") or item.get("track") or item.get("data") or item

    latest_status = track.get("latest_status") or {}
    latest_event_raw = track.get("latest_event")
    latest_event = latest_event_raw if isinstance(latest_event_raw, dict) else {}
    latest_event_text = _text(latest_event_raw) if latest_event_raw and not isinstance(latest_event_raw, dict) else ""

    main_status = _optional_text(
        latest_status.get("status")
        or track.get("z0")
        or track.get("main_status")
    )
    status_text = _text(
        latest_event.get("description")
        or latest_status.get("sub_status_descr")
        or track.get("z1")
        or latest_event_text
        or track.get("latest_event")
        or "No tracking updates"
    )
    provider_status_description = (
        track.get("provider_status_description")
        or latest_event.get("description")
        or latest_status.get("sub_status_descr")
        or latest_event_text
        or status_text
    )
    provider_status_description = _text(provider_status_description)
    normalized_status = normalize_status(main_status, None, status_text)

    shipping_info = track.get("shipping_info") or {}
    origin_country = _optional_text(
        shipping_info.get("shipper_address", {}).get("country")
        or track.get("origin_info", {}).get("item_pre_advice")
        or track.get("origin_country")
        or None
    )
    destination_country = _optional_text(
        shipping_info.get("recipient_address", {}).get("country")
        or track.get("destination_info", {}).get("item_dest_country")
        or track.get("destination_country")
        or None
    )

    tracking_data = track.get("tracking") or {}
    provider_entries = tracking_data.get("providers") or [] if isinstance(tracking_data, dict) else []
    if provider_entries:
        primary_provider = provider_entries[0]
        provider_meta = primary_provider.get("provider") or {}
        raw_events = primary_provider.get("events") or []
        carrier_name = (
            provider_meta.get("name")
            or provider_meta.get("alias")
            or item.get("carrier_name")
            or item.get("carrier")
        )
    else:
        raw_events = tracking_data if isinstance(tracking_data, list) else (track.get("events") or [])
        carrier_name = item.get("carrier_name") or item.get("carrier")

    events = []
    for event in raw_events:
        provider_status = _text(
            event.get("stage")
            or event.get("status")
            or ""
        )
        event_description = _text(
            event.get("description")
            or event.get("status")
            or event.get("stage")
            or ""
        )
        event_normalized_status = normalize_status(provider_status, None, event_description)
        event_time = _text(
            event.get("time_utc")
            or event.get("time_iso")
            or event.get("eventTime")
            or event.get("time")
            or ""
        )
        location_value = (
            event.get("location")
            or event.get("address", {}).get("city")
            or ""
        )
        events.append(
            {
                "time": event_time,
                "eventTime": event_time,
                "location": _text(location_value),
                "description": event_description,
                "raw_status": provider_status,
                "providerStatus": provider_status,
                "providerStatusDescription": event_description,
                "normalizedStatus": event_normalized_status,
            }
        )

    last_event_time = _optional_text(
        latest_event.get("time_utc")
        or latest_event.get("time_iso")
        or (events[0]["time"] if events else None)
    )
    return {
        "tracking_number": tracking_number,
        "carrier_code": _optional_text(item.get("carrier") or item.get("carrier_code")),
        "carrier_name": _optional_text(carrier_name),
        "normalized_status": normalized_status,
        "status_text": status_text,
        "provider_status": main_status,
        "provider_status_description": provider_status_description,
        "origin_country": origin_country,
        "destination_country": destination_country,
        "last_event_time": last_event_time,
        "events": events,
        "raw_response": raw_response,
    }
