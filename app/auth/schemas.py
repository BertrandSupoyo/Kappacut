"""Pydantic schemas fastapi-users serialises users through."""
from __future__ import annotations

import uuid

from fastapi_users import schemas
from pydantic import Field


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None
    # the sign-up tick box. UserManager.create checks it; exclude keeps it out of the dict
    # that becomes the users row, which has no such column
    accept_terms: bool = Field(default=False, exclude=True)


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
