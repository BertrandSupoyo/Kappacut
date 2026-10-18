"""User database adapter + UserManager (register/verify/reset hooks)."""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.email import send_reset_email, send_verify_email
from app.models.user import User

log = logging.getLogger("clipfinder.auth")


async def get_user_db(session: AsyncSession = Depends(get_session)) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.reset_password_token_secret
    verification_token_secret = settings.verification_token_secret

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        log.info("registered %s", user.email)
        if user.is_verified:            # e.g. created by the admin CLI
            return
        try:
            await self.request_verify(user, request)
        except Exception as e:  # noqa: BLE001
            log.warning("could not start verification for %s: %s", user.email, e)

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        await send_verify_email(user.email, token)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        await send_reset_email(user.email, token)

    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        log.info("verified %s", user.email)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)
