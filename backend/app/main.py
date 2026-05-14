from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
from .internal_auth import verify_internal_token
from .db import init_db
from .rate_limit import enforce_ip_limits
from .schemas import InternalTrackRequest, RecentShipmentsResponse, TrackResponse
from .services import get_recent_shipments, parse_tracking_numbers, query_order_tracking, query_tracking_numbers
from .seventeen_track import SeventeenTrackClient
from .shopify_proxy import verify_proxy_request

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
client = SeventeenTrackClient()
STATIC_DIR = ROOT_DIR / "backend" / "app" / "static"
app.mount("/internal/static", StaticFiles(directory=STATIC_DIR / "internal"), name="internal_static")


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

    shipments, errors = query_tracking_numbers(
        client,
        tracking_numbers,
        carrier,
        shop_domain,
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


@app.get("/api/order-track", response_model=TrackResponse)
def track_by_order(
    request: Request,
    order_no: str = Query(..., description="Store order number."),
    email: str = Query(..., description="Order email address."),
) -> TrackResponse:
    shop_domain = verify_proxy_request(request)
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    enforce_ip_limits(client_ip)

    shipments, errors = query_order_tracking(
        client,
        order_no,
        email,
        shop_domain,
    )

    return TrackResponse(
        success=not errors,
        ok=not errors,
        queryCount=1,
        shopDomain=shop_domain,
        generatedAt=datetime.now(timezone.utc),
        shipments=shipments,
        errors=errors,
    )


@app.get("/internal")
@app.get("/internal/")
def internal_console() -> FileResponse:
    return FileResponse(Path(STATIC_DIR / "internal" / "index.html"))


@app.post("/internal/api/track", response_model=TrackResponse)
def internal_track(request: Request, payload: InternalTrackRequest) -> TrackResponse:
    verify_internal_token(request)
    tracking_numbers = parse_tracking_numbers(payload.nums)
    if not tracking_numbers:
        raise HTTPException(status_code=400, detail="No valid tracking numbers were provided.")

    shipments, errors = query_tracking_numbers(
        client,
        tracking_numbers,
        payload.carrier,
        None,
        enforce_order_match=False,
    )
    return TrackResponse(
        success=not errors,
        ok=not errors,
        queryCount=len(tracking_numbers),
        shopDomain=None,
        generatedAt=datetime.now(timezone.utc),
        shipments=shipments,
        errors=errors,
    )


@app.get("/internal/api/recent", response_model=RecentShipmentsResponse)
def internal_recent(
    request: Request,
    limit: int = Query(default=12, ge=1, le=100),
) -> RecentShipmentsResponse:
    verify_internal_token(request)
    shipments = get_recent_shipments(limit)
    return RecentShipmentsResponse(count=len(shipments), shipments=shipments)
