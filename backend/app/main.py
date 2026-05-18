from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT_DIR, settings
from .internal_auth import verify_internal_token
from .db import init_db, summarize_daily_usage, summarize_system_events
from .observability import log_event, send_alert, send_daily_usage_report
from .rate_limit import enforce_ip_limits
from .schemas import (
    DailyUsageSummaryResponse,
    InternalOrderTrackRequest,
    InternalTrackRequest,
    OpsSummaryResponse,
    RecentShipmentsResponse,
    TrackResponse,
)
from .services import get_recent_shipments, parse_tracking_numbers, query_order_tracking, query_tracking_numbers
from .seventeen_track import SeventeenTrackClient
from .shopify_oauth import (
    build_authorize_url,
    complete_oauth_install,
    exchange_code_for_offline_token,
    validate_shop_domain,
    verify_oauth_callback,
)
from .shopify_proxy import verify_proxy_request
from .shopify_webhooks import parse_webhook_payload, sync_tracking_mappings_from_webhook, verify_webhook_request

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
client = SeventeenTrackClient()
STATIC_DIR = ROOT_DIR / "backend" / "app" / "static"
app.mount("/internal/static", StaticFiles(directory=STATIC_DIR / "internal"), name="internal_static")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = uuid4().hex[:12]
    request.state.request_id = request_id
    start = perf_counter()
    response = None
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        duration_ms = round((perf_counter() - start) * 1000, 2)
        log_event(
            "request_exception",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            client_ip=client_ip,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        send_alert(
            "request_exception",
            f"{request.method} {request.url.path} raised {type(exc).__name__}",
            request_id=request_id,
            path=request.url.path,
            query=request.url.query,
            client_ip=client_ip,
            error=str(exc),
        )
        raise
    finally:
        if response is not None:
            duration_ms = round((perf_counter() - start) * 1000, 2)
            log_event(
                "request_complete",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                query=request.url.query,
                client_ip=client_ip,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            if response.status_code >= 500:
                send_alert(
                    "request_5xx",
                    f"{request.method} {request.url.path} returned {response.status_code}",
                    request_id=request_id,
                    path=request.url.path,
                    query=request.url.query,
                    client_ip=client_ip,
                    status_code=response.status_code,
                )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/shopify/auth/start")
def shopify_auth_start(
    request: Request,
    shop: str = Query(..., description="Target Shopify shop domain, e.g. example.myshopify.com"),
) -> RedirectResponse:
    shop_domain = validate_shop_domain(shop)
    authorize_url, nonce = build_authorize_url(request, shop_domain)
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        "shopify_oauth_nonce",
        nonce,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.shopify_oauth_state_ttl_seconds,
    )
    return response


@app.get("/api/shopify/auth/callback", response_class=HTMLResponse)
def shopify_auth_callback(request: Request) -> HTMLResponse:
    shop_domain, code = verify_oauth_callback(request)
    token_payload = exchange_code_for_offline_token(
        shop_domain,
        code,
        str(request.url_for("shopify_auth_callback")),
    )
    result = complete_oauth_install(shop_domain, token_payload)
    response = HTMLResponse(
        (
            "<html><body style='font-family:sans-serif;padding:32px;'>"
            "<h2>Shopify authorization completed</h2>"
            f"<p>Shop: <strong>{result['shopDomain']}</strong></p>"
            f"<p>Granted scopes: {', '.join(result['grantedScopes']) or '(none)'}</p>"
            f"<p>Missing required scopes: {', '.join(result['missingScopes']) or '(none)'}</p>"
            "<p>You can close this tab and return to Shopify.</p>"
            "</body></html>"
        )
    )
    response.delete_cookie("shopify_oauth_nonce")
    return response


@app.post("/api/shopify/webhooks")
async def shopify_webhook(request: Request) -> dict[str, object]:
    raw_body = await request.body()
    shop_domain, topic = verify_webhook_request(request, raw_body)
    payload = parse_webhook_payload(raw_body)
    result = sync_tracking_mappings_from_webhook(topic, shop_domain, payload)
    return {"ok": True, **result}


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
        log_event(
            "tracking_input_invalid",
            level="warning",
            message="No valid tracking numbers were provided.",
            client_ip=client_ip,
            shop_domain=shop_domain,
            raw_nums=nums,
        )
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
    email: str | None = Query(default=None, description="Optional order email address."),
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
        log_event(
            "internal_tracking_input_invalid",
            level="warning",
            message="No valid tracking numbers were provided to the internal console.",
            raw_nums=payload.nums,
        )
        raise HTTPException(status_code=400, detail="No valid tracking numbers were provided.")

    shipments, errors = query_tracking_numbers(
        client,
        tracking_numbers,
        payload.carrier,
        payload.shop_domain,
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


@app.post("/internal/api/order-track", response_model=TrackResponse)
def internal_track_by_order(request: Request, payload: InternalOrderTrackRequest) -> TrackResponse:
    verify_internal_token(request)

    shipments, errors = query_order_tracking(
        client,
        payload.order_no,
        payload.email,
        payload.shop_domain,
    )

    return TrackResponse(
        success=not errors,
        ok=not errors,
        queryCount=1,
        shopDomain=payload.shop_domain,
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


@app.get("/internal/api/ops/summary", response_model=OpsSummaryResponse)
def internal_ops_summary(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> OpsSummaryResponse:
    verify_internal_token(request)
    return OpsSummaryResponse(**summarize_system_events(limit))


@app.get("/internal/api/ops/daily-usage", response_model=DailyUsageSummaryResponse)
def internal_daily_usage_summary(
    request: Request,
    day: str | None = Query(default=None, description="Optional UTC date in YYYY-MM-DD format."),
) -> DailyUsageSummaryResponse:
    verify_internal_token(request)
    return DailyUsageSummaryResponse(**summarize_daily_usage(day), notes=[])


@app.post("/internal/api/ops/daily-usage-alert", response_model=DailyUsageSummaryResponse)
def internal_daily_usage_alert(
    request: Request,
    day: str | None = Query(default=None, description="Optional UTC date in YYYY-MM-DD format."),
) -> DailyUsageSummaryResponse:
    verify_internal_token(request)
    return DailyUsageSummaryResponse(**send_daily_usage_report(day))
