"""Authentication — fastapi-users 15 with a cookie (httpOnly) JWT backend.

  schemas.py   UserRead / UserCreate / UserUpdate
  manager.py   get_user_db, UserManager (email hooks), get_user_manager
  backend.py   cookie transport + JWT strategy + FastAPIUsers + current_user deps
  routes.py    mounts /api/auth/* and /api/users/* on the app
"""
from app.auth.backend import (
    auth_backend,
    current_active_user,
    current_optional_user,
    current_superuser,
    current_verified_user,
    fastapi_users,
)

__all__ = [
    "auth_backend",
    "fastapi_users",
    "current_active_user",
    "current_verified_user",
    "current_superuser",
    "current_optional_user",
]
