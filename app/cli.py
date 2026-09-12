"""Small operational CLI.

  python -m app.cli create-superuser --email you@example.com --password ...
  python -m app.cli run-gc
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib

from sqlalchemy import select

from app.db import async_session_maker, engine


async def _create_superuser(email: str, password: str) -> None:
    from app.auth.manager import UserManager, get_user_db
    from app.auth.schemas import UserCreate

    async with async_session_maker() as session:
        user_db = None
        async for db in get_user_db(session):  # noqa: B007
            user_db = db
            break
        manager = UserManager(user_db)
        existing = (await session.execute(
            select(user_db.user_table).where(user_db.user_table.email == email)
        )).scalar_one_or_none()
        if existing:
            existing.is_superuser = True
            existing.is_verified = True
            existing.is_active = True
            await session.commit()
            print(f"promoted existing user {email} to verified superuser")
            return
        user = await manager.create(
            UserCreate(email=email, password=password, is_superuser=True, is_verified=True),
            safe=False,
        )
        print(f"created superuser {user.email} ({user.id})")


async def _run_gc() -> None:
    from app.retention import run_gc

    print(await run_gc())


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    su = sub.add_parser("create-superuser")
    su.add_argument("--email", required=True)
    su.add_argument("--password", required=True)
    sub.add_parser("run-gc")
    args = ap.parse_args()

    async def _run() -> None:
        try:
            if args.cmd == "create-superuser":
                await _create_superuser(args.email, args.password)
            elif args.cmd == "run-gc":
                await _run_gc()
        finally:
            with contextlib.suppress(Exception):
                await engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
