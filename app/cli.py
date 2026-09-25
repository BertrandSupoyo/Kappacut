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


def _migrate_media(dry_run: bool) -> None:
    """One-shot copy of the old local media volume into the bucket (PLAN-v2.md Phase A).

    The bucket layout is identical to the volume layout, so this is a straight walk. It is
    idempotent — an object already present with the same size is skipped — so it is safe to
    run twice, and safe to re-run after a partial failure.
    """
    from app import storage
    from app.config import settings

    root = settings.media_root.resolve()
    if not root.is_dir():
        print(f"nothing to migrate: {root} does not exist")
        return

    copied = skipped = failed = 0
    copied_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(root).as_posix()
        if key.startswith("_uploads/"):
            continue                      # abandoned tusd partials; not worth carrying over
        size = path.stat().st_size
        try:
            existing = storage.head(key)
            if existing and int(existing["ContentLength"]) == size:
                skipped += 1
                continue
            if not dry_run:
                storage.put_file(key, path)
            copied += 1
            copied_bytes += size
        except Exception as e:  # noqa: BLE001 — report and keep going; re-run to retry
            print(f"  FAILED {key}: {e}")
            failed += 1

    verb = "would copy" if dry_run else "copied"
    print(f"{verb} {copied} object(s), {copied_bytes / 1024**3:.2f} GiB · "
          f"skipped {skipped} already present · {failed} failed")
    if failed:
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    su = sub.add_parser("create-superuser")
    su.add_argument("--email", required=True)
    su.add_argument("--password", required=True)
    sub.add_parser("run-gc")
    mm = sub.add_parser("migrate-media", help="copy the local media volume into the bucket")
    mm.add_argument("--dry-run", action="store_true", help="report what would be copied")
    args = ap.parse_args()

    async def _run() -> None:
        try:
            if args.cmd == "create-superuser":
                await _create_superuser(args.email, args.password)
            elif args.cmd == "run-gc":
                await _run_gc()
            elif args.cmd == "migrate-media":
                _migrate_media(args.dry_run)
        finally:
            with contextlib.suppress(Exception):
                await engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
