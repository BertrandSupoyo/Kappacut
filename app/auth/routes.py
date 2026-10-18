"""Mount the fastapi-users routers onto the app."""
from __future__ import annotations

from fastapi import Depends, FastAPI

from app.auth.backend import auth_backend, fastapi_users
from app.auth.schemas import UserCreate, UserRead, UserUpdate
from app.ratelimit import rate_limit


def install_auth(app: FastAPI) -> None:
    # login / logout — requires a verified email
    app.include_router(
        fastapi_users.get_auth_router(auth_backend, requires_verification=True),
        prefix="/api/auth",
        tags=["auth"],
        dependencies=[Depends(rate_limit("login", 10, 15 * 60))],
    )
    # register
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/api/auth",
        tags=["auth"],
        dependencies=[Depends(rate_limit("register", 5, 60 * 60))],
    )
    # request-verify-token / verify
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix="/api/auth",
        tags=["auth"],
        dependencies=[Depends(rate_limit("verify", 10, 60 * 60))],
    )
    # forgot-password / reset-password
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix="/api/auth",
        tags=["auth"],
        dependencies=[Depends(rate_limit("reset", 5, 60 * 60))],
    )
    # GET/PATCH /api/users/me  (+ superuser GET/PATCH/DELETE /api/users/{id})
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate, requires_verification=True),
        prefix="/api/users",
        tags=["users"],
    )
