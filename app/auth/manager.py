"""User database adapter + UserManager (register/verify/reset hooks)."""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import TERMS_VERSION, settings
from app.db import get_session
from app.email import send_reset_email, send_verify_email
from app.models.launch import Invite
from app.models.user import User

log = logging.getLogger("kappacut.auth")


async def get_user_db(session: AsyncSession = Depends(get_session)) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.reset_password_token_secret
    verification_token_secret = settings.verification_token_secret

    async def create(self, user_create, safe: bool = False, request: Request | None = None) -> User:
        # safe = someone signing up through the API. The admin CLI passes safe=False: it makes
        # the first admin, who nobody could have invited yet.
        if not safe:
            return await super().create(user_create, safe, request)
        if not user_create.accept_terms:
            raise HTTPException(400, "Please accept the Terms and Privacy Policy to create an account.")
        if settings.invite_only:
            invited = await self.user_db.session.scalar(
                select(Invite.id).where(Invite.email == user_create.email))
            if invited is None:
                raise HTTPException(403, "Kappacut is invite-only right now, and this email isn't on the list.")
        user = await super().create(user_create, safe, request)
        return await self.user_db.update(user, {"terms_accepted_at": dt.datetime.now(dt.UTC),
                                                "terms_version": TERMS_VERSION})

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
