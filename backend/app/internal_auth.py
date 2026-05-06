from __future__ import annotations

from fastapi import HTTPException, Request

from .config import settings


def verify_internal_token(request: Request) -> None:
    if not settings.internal_dashboard_token:
        raise HTTPException(status_code=503, detail="Missing INTERNAL_DASHBOARD_TOKEN configuration.")

    provided = request.headers.get("x-internal-token") or request.query_params.get("token")
    if not provided or provided != settings.internal_dashboard_token:
        raise HTTPException(status_code=401, detail="Invalid internal dashboard token.")
