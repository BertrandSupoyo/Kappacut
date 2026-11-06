"""Shared-Groq-key protection: a per-minute token bucket in Redis that every worker
draws from before a chat completion, so concurrent analyses can't collectively blow the
free-tier TPM limit. Sync (called from the ffmpeg/SDK worker thread). Fail-open.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import redis

from app.config import settings

log = logging.getLogger("kappacut.groq_gate")

_r = redis.Redis.from_url(settings.redis_url)
_MAX_WAIT_S = 180


def _noop(_actual: int) -> None:
    return None


def _refund(key: str, bucket: int, reserved: int, actual: int) -> None:
    """Give back what a call reserved but didn't use - only into the minute it was taken
    from (an older minute's budget no longer matters, and DECRBY would recreate a key that
    already expired)."""
    unused = reserved - max(0, int(actual or 0))
    if unused <= 0 or int(time.time() // 60) != bucket:
        return
    try:
        if _r.exists(key):
            _r.decrby(key, unused)
    except Exception:  # noqa: BLE001
        pass


def gate(est_tokens: int = 6000, model: str = "",
         on_wait: Callable[[], None] | None = None, max_wait: float = _MAX_WAIT_S) -> Callable[[int], None]:
    """Block until `est_tokens` fit in the current minute's Groq budget for `model`.
    Returns `settle(actual_tokens)`, to call with the reply's real usage.

    The reservation has to be the worst case (prompt + the whole max_tokens reply), but a
    low-effort JSON reply uses a fraction of it. Without settling, every call held ~7k of the
    8k budget for the whole minute: one call a minute, and a 20-minute video sat on
    "Ranking the moments" for ~8 minutes looking stuck.

    Groq's TPM limit is per model, so the picker and the judge each get their own bucket
    instead of queueing behind each other's calls.
    """
    ceiling = max(1000, int(settings.groq_tokens_per_minute))
    est_tokens = max(1, min(int(est_tokens), ceiling))
    deadline = time.monotonic() + max_wait
    told = False
    while True:
        bucket = int(time.time() // 60)
        key = f"groq:tok:{model or 'any'}:{bucket}"
        try:
            used = _r.incrby(key, est_tokens)
            if used == est_tokens:
                _r.expire(key, 120)
            if used <= ceiling:
                return lambda actual, _k=key, _b=bucket: _refund(_k, _b, est_tokens, actual)
            # over budget: give the token back and wait for the window to roll
            _r.decrby(key, est_tokens)
        except Exception as e:  # noqa: BLE001 — Redis down must not wedge analysis
            log.warning("groq gate bypassed: %s", e)
            return _noop
        if time.monotonic() >= deadline:
            log.warning("groq gate: waited %ds, proceeding anyway", max_wait)
            return _noop
        if on_wait and not told:          # say so once: a silent wait reads as "stuck"
            told = True
            try:
                on_wait()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(min(5.0, 61 - (time.time() % 60)))


def install() -> None:
    """Point kappacut's optional hook at this gate (called on worker startup)."""
    import kappacut as cf

    cf.GROQ_GATE = gate
