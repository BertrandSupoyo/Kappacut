"""clipfinder multi-user backend.

Package layout (built phase by phase — see PLAN-multiuser.md):
  config.py   Settings (env) — infra + limits
  db.py       async SQLAlchemy engine + session dependency + Base
  models/     ORM models (Phase 1: users; Phase 3: projects, jobs, ...)
  main.py     FastAPI app + lifespan + /api/health
  legacy.py   the pre-migration routes, kept working until each phase retires them
"""
