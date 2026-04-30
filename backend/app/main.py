from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request

from .config import settings
from .db import init_db
from .rate_limit import enforce_ip_limits
from .schemas import QueryError, TrackResponse
from .services import parse_tracking_numbers, process_tracking_number
from .seventeen_track import SeventeenTrackClient
from .shopify_proxy import verify_proxy_request

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
client = SeventeenTrackClient()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/track", response_model=TrackResponse)
def track(
    request: Request,
    nums: str = Query(..., description="Comma or whitespace separated tracking numbers."),
    carrier: str | None = Query(default=None),
) -> TrackResponse:
    shop_domain = verify_proxy_request(request)
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    enforce_ip_limits(client_ip)

    tracking_numbers = parse_tracking_numbers(nums)
    if not tracking_numbers:
        raise HTTPException(status_code=400, detail="No valid tracking numbers were provided.")

    shipments = []
    errors: list[QueryError] = []
    for tracking_number in tracking_numbers:
        try:
            shipment, error = process_tracking_number(client, tracking_number, carrier, shop_domain)
            if shipment:
                shipments.append(shipment)
            if error:
                errors.append(error)
        except HTTPException as exc:
            errors.append(
                QueryError(
                    trackingNumber=tracking_number,
                    code="query_error",
                    message=str(exc.detail),
                )
            )

    return TrackResponse(
        success=not errors,
        ok=not errors,
        queryCount=len(tracking_numbers),
        shopDomain=shop_domain,
        generatedAt=datetime.now(timezone.utc),
        shipments=shipments,
        errors=errors,
    )
