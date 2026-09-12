"""Cookie (httpOnly) JWT authentication backend + current-user dependencies."""
from __future__ import annotations

import uuid

from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy

from app.auth.manager import get_user_manager
from app.config import settings
from app.models.user import User

# The browser SPA can't attach Authorization headers to <video>/<img>/EventSource,
# so the token rides in an httpOnly cookie. CSRF is handled by app/csrf.py.
cookie_transport = CookieTransport(
    cookie_name="cfa",
    cookie_max_age=settings.access_token_ttl_seconds,
    cookie_secure=settings.cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.access_token_ttl_seconds,
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_verified_user = fastapi_users.current_user(active=True, verified=True)
current_superuser = fastapi_users.current_user(active=True, verified=True, superuser=True)
current_optional_user = fastapi_users.current_user(active=True, optional=True)
