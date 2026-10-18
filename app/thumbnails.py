"""AI thumbnail generation — multiple image-gen providers, combined by measured performance
rather than a single hardcoded choice.

Groq has no image-generation capability (verified against its own API docs), so this is a
new class of external call for clipfinder: real per-image spend, gated behind
app/credits.py's check_credits() (Phase E), not the free-tier Groq quotas.

Each provider is a plain async `generate(prompt) -> bytes` function behind a common
interface. Redis holds a rolling success-rate + latency EMA per provider (same
fail-open, best-effort spirit as app/groq_gate.py's token bucket); generate_thumbnail()
always tries the CURRENTLY best-performing provider first and falls back to the next on
failure, so a provider that starts erroring or slowing down loses traffic share on its
own — nobody has to notice and flip a setting.

Provider shapes below are grounded in each vendor's published HTTP docs (fetched during
Phase-boost planning), not a live-verified call with a real key — smoke-test with a real
key before trusting a provider in production; a wrong field name just makes that
provider fail its own calls, which the performance router already routes around.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

import httpx
import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger("clipfinder.thumbnails")
_r = aioredis.from_url(settings.redis_url, decode_responses=True)

_STATS_TTL_S = 30 * 24 * 3600   # a month of rolling history is plenty to judge "current" performance


class ProviderError(Exception):
    """A provider failed or isn't configured — generate_thumbnail() catches this and
    falls back to the next-best provider rather than letting it propagate."""


async def _gen_together(prompt: str) -> bytes:
    if not settings.together_api_key:
        raise ProviderError("together: no API key configured")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.together.ai/v1/images/generations",
            headers={"Authorization": f"Bearer {settings.together_api_key}"},
            json={"model": "black-forest-labs/FLUX.1-schnell", "prompt": prompt,
                  "width": 768, "height": 1024, "n": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        url = data["data"][0]["url"]
        img = await client.get(url)
        img.raise_for_status()
        return img.content


async def _gen_replicate(prompt: str) -> bytes:
    """Replicate is async-by-default; `Prefer: wait` blocks the one POST until the
    prediction finishes instead of a separate poll loop (per replicate.com/docs)."""
    if not settings.replicate_api_key:
        raise ProviderError("replicate: no API key configured")
    async with httpx.AsyncClient(timeout=65.0) as client:
        resp = await client.post(
            "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
            headers={"Authorization": f"Bearer {settings.replicate_api_key}", "Prefer": "wait"},
            json={"input": {"prompt": prompt, "aspect_ratio": "9:16"}},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "succeeded":
            raise ProviderError(f"replicate: status={data.get('status')} error={data.get('error')}")
        output = data.get("output")
        url = output[0] if isinstance(output, list) else output
        if not url:
            raise ProviderError("replicate: no output url in response")
        img = await client.get(url)
        img.raise_for_status()
        return img.content


Generator = Callable[[str], Awaitable[bytes]]
PROVIDERS: dict[str, Generator] = {"together": _gen_together, "replicate": _gen_replicate}
# which Settings field holds each provider's key — lets rank_providers() skip unconfigured ones
_KEY_SETTING: dict[str, str] = {"together": "together_api_key", "replicate": "replicate_api_key"}

_EMA_ALPHA = 0.3   # weight on the newest sample; lower = steadier, slower to react


def _stats_key(provider: str) -> str:
    return f"imggen:stats:{provider}"


async def _record(provider: str, ok: bool, latency_ms: float) -> None:
    try:
        key = _stats_key(provider)
        raw = await _r.hgetall(key)
        success = int(raw.get("success", 0)) + (1 if ok else 0)
        fail = int(raw.get("fail", 0)) + (0 if ok else 1)
        prev_ema = float(raw["latency_ema_ms"]) if raw.get("latency_ema_ms") else latency_ms
        ema = _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * prev_ema
        await _r.hset(key, mapping={"success": success, "fail": fail, "latency_ema_ms": ema})
        await _r.expire(key, _STATS_TTL_S)
    except Exception as e:  # noqa: BLE001 — stats are advisory, never block a real attempt
        log.warning("thumbnails: couldn't record perf stats for %s: %s", provider, e)


def _score(success: int, fail: int, latency_ema_ms: float) -> tuple[float, float]:
    """(success_rate desc, latency asc) — Laplace-smoothed so an untested provider starts
    neutral (0.5) rather than 0, and only proven track records outrank it."""
    rate = (success + 1) / (success + fail + 2)
    return (-rate, latency_ema_ms)


async def rank_providers() -> list[str]:
    """Configured providers ordered best-first by recent measured performance."""
    configured = [p for p in PROVIDERS if getattr(settings, _KEY_SETTING[p])]
    scored = []
    for p in configured:
        raw = await _r.hgetall(_stats_key(p))
        success, fail = int(raw.get("success", 0)), int(raw.get("fail", 0))
        latency = float(raw["latency_ema_ms"]) if raw.get("latency_ema_ms") else 5000.0
        scored.append((p, _score(success, fail, latency)))
    scored.sort(key=lambda x: x[1])
    return [p for p, _ in scored]


async def generate_thumbnail(prompt: str) -> bytes:
    """Try configured providers best-performing-first, recording each attempt's outcome,
    falling back on failure. Raises ProviderError only if every configured provider fails
    (or none are configured at all)."""
    order = await rank_providers()
    if not order:
        raise ProviderError("no image-gen provider is configured (set TOGETHER_API_KEY or "
                             "REPLICATE_API_KEY)")
    last_err: Exception | None = None
    for provider in order:
        t0 = time.monotonic()
        try:
            img = await PROVIDERS[provider](prompt)
            await _record(provider, True, (time.monotonic() - t0) * 1000)
            return img
        except Exception as e:  # noqa: BLE001 — try the next provider instead of failing outright
            await _record(provider, False, (time.monotonic() - t0) * 1000)
            log.warning("thumbnails: provider %s failed, trying next: %s", provider, e)
            last_err = e
    raise ProviderError(f"every configured image-gen provider failed; last error: {last_err}")
