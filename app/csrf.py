"""Double-submit CSRF protection for cookie-authenticated mutations.

Cookies auto-send, so an unsafe request under /api must also echo the non-httpOnly
`csrftoken` cookie in an `X-CSRF-Token` header. /api/auth/* is exempt (pre-session or
token-based). SameSite=Lax already blocks cross-site POSTs; this is defense in depth.
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

_SAFE = {"GET", "HEAD", "OPTIONS", "TRACE"}
# /api/auth/* — pre-session or token-based; /api/upload/hooks — internal call from tusd
_EXEMPT_PREFIXES = ("/api/auth/", "/api/upload/hooks")
_COOKIE = "csrftoken"
_WEEK = 60 * 60 * 24 * 7


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        guarded = (
            request.method not in _SAFE
            and path.startswith("/api/")
            and not path.startswith(_EXEMPT_PREFIXES)
        )
        if guarded:
            cookie = request.cookies.get(_COOKIE)
            header = request.headers.get("x-csrf-token")
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)

        response = await call_next(request)
        if _COOKIE not in request.cookies:
            response.set_cookie(
                _COOKIE, secrets.token_urlsafe(32),
                max_age=_WEEK, samesite="lax",
                secure=settings.cookie_secure, httponly=False, path="/",
            )
        return response
