"""Shared-Groq-key protection: a per-minute token bucket in Redis that every worker
draws from before a chat completion, so concurrent analyses can't collectively blow the
free-tier TPM limit. Sync (called from the ffmpeg/SDK worker thread). Fail-open.
"""
from __future__ import annotations

import logging
import time

import redis

from app.config import settings

log = logging.getLogger("clipfinder.groq_gate")

_r = redis.Redis.from_url(settings.redis_url)
_MAX_WAIT_S = 180


def gate(est_tokens: int = 6000) -> None:
    """Block until `est_tokens` fit in the current minute's Groq budget."""
    ceiling = max(1000, int(settings.groq_tokens_per_minute))
    est_tokens = max(1, min(int(est_tokens), ceiling))
    deadline = time.monotonic() + _MAX_WAIT_S
    while True:
        bucket = int(time.time() // 60)
        key = f"groq:tok:{bucket}"
        try:
            used = _r.incrby(key, est_tokens)
            if used == est_tokens:
                _r.expire(key, 120)
            if used <= ceiling:
                return
            # over budget: give the token back and wait for the window to roll
            _r.decrby(key, est_tokens)
        except Exception as e:  # noqa: BLE001 — Redis down must not wedge analysis
            log.warning("groq gate bypassed: %s", e)
            return
        if time.monotonic() >= deadline:
            log.warning("groq gate: waited %ds, proceeding anyway", _MAX_WAIT_S)
            return
        time.sleep(min(5.0, 61 - (time.time() % 60)))


def install() -> None:
    """Point clipfinder's optional hook at this gate (called on worker startup)."""
    import clipfinder as cf

    cf.GROQ_GATE = gate
