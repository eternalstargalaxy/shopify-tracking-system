from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException, Request as FastAPIRequest

from .config import settings
from .db import upsert_shopify_installation
from .observability import log_event

SHOP_DOMAIN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")


def normalize_shop_domain(shop_domain: str | None) -> str:
    return (shop_domain or "").strip().lower()


def validate_shop_domain(shop_domain: str | None) -> str:
    normalized = normalize_shop_domain(shop_domain)
    if not SHOP_DOMAIN_PATTERN.match(normalized):
        raise HTTPException(status_code=400, detail="Invalid Shopify shop domain.")
    if settings.allowed_shop_domains and normalized not in settings.allowed_shop_domains:
        raise HTTPException(status_code=403, detail="Shop domain is not allowed.")
    return normalized


def build_authorize_url(request: FastAPIRequest, shop_domain: str) -> tuple[str, str]:
    if not settings.shopify_client_id or not settings.shopify_client_secret:
        raise HTTPException(status_code=500, detail="Missing Shopify OAuth credentials.")

    nonce = base64.urlsafe_b64encode(hashlib.sha256(f"{shop_domain}:{time.time()}".encode("utf-8")).digest())[:24].decode("ascii")
    state = _encode_state(
        {
            "shop": shop_domain,
            "nonce": nonce,
            "ts": int(time.time()),
        }
    )
    query = urlencode(
        {
            "client_id": settings.shopify_client_id,
            "scope": ",".join(settings.shopify_required_scopes),
            "redirect_uri": str(request.url_for("shopify_auth_callback")),
            "state": state,
        }
    )
    return f"https://{shop_domain}/admin/oauth/authorize?{query}", nonce


def verify_oauth_callback(request: FastAPIRequest) -> tuple[str, str]:
    if not settings.shopify_client_secret:
        raise HTTPException(status_code=500, detail="Missing Shopify client secret.")

    params = dict(request.query_params)
    received_hmac = params.pop("hmac", "")
    params.pop("signature", None)
    if not received_hmac:
        raise HTTPException(status_code=401, detail="Missing Shopify OAuth signature.")

    message = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
    )
    computed = hmac.new(
        settings.shopify_client_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, received_hmac):
        raise HTTPException(status_code=401, detail="Invalid Shopify OAuth signature.")

    payload = _decode_state(request.query_params.get("state", ""))
    shop_domain = validate_shop_domain(request.query_params.get("shop"))
    if payload.get("shop") != shop_domain:
        raise HTTPException(status_code=400, detail="Shopify OAuth state shop mismatch.")
    nonce_cookie = request.cookies.get("shopify_oauth_nonce", "")
    if not nonce_cookie or nonce_cookie != payload.get("nonce"):
        raise HTTPException(status_code=400, detail="Shopify OAuth state mismatch.")
    if int(time.time()) - int(payload.get("ts") or 0) > settings.shopify_oauth_state_ttl_seconds:
        raise HTTPException(status_code=400, detail="Shopify OAuth state expired.")

    code = (request.query_params.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Missing Shopify authorization code.")

    return shop_domain, code


def exchange_code_for_offline_token(shop_domain: str, code: str, redirect_uri: str) -> dict[str, Any]:
    payload = urlencode(
        {
            "client_id": settings.shopify_client_id,
            "client_secret": settings.shopify_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        url=f"https://{shop_domain}/admin/oauth/access_token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Shopify token exchange failed: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="Shopify token exchange network error.") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid Shopify token exchange response.") from exc
    if not parsed.get("access_token"):
        raise HTTPException(status_code=502, detail="Shopify token exchange did not return an access token.")
    return parsed


def complete_oauth_install(shop_domain: str, token_payload: dict[str, Any]) -> dict[str, Any]:
    access_token = str(token_payload.get("access_token") or "").strip()
    scope = str(token_payload.get("scope") or "").strip()
    upsert_shopify_installation(shop_domain, access_token, scope)
    granted_scopes = {item.strip() for item in scope.split(",") if item.strip()}
    missing_scopes = [
        scope_name
        for scope_name in settings.shopify_required_scopes
        if scope_name not in granted_scopes
    ]
    log_event(
        "shopify_oauth_installed",
        message="Stored Shopify offline access token after OAuth callback.",
        shop_domain=shop_domain,
        granted_scopes=sorted(granted_scopes),
        missing_scopes=missing_scopes,
    )
    return {
        "shopDomain": shop_domain,
        "grantedScopes": sorted(granted_scopes),
        "missingScopes": missing_scopes,
    }


def _encode_state(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.shopify_client_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def _decode_state(value: str) -> dict[str, Any]:
    encoded_payload, _, signature = value.partition(".")
    if not encoded_payload or not signature:
        raise HTTPException(status_code=400, detail="Invalid Shopify OAuth state.")
    computed = hmac.new(
        settings.shopify_client_secret.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, signature):
        raise HTTPException(status_code=400, detail="Invalid Shopify OAuth state signature.")
    padding = "=" * (-len(encoded_payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded_payload + padding)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Shopify OAuth state payload.") from exc
