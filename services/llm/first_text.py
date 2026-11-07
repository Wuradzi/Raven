"""Bounded wait for a provider's first text (roadmap phase 4, live test E274).

A provider that has produced nothing yet may be abandoned for a backup: nothing was
heard, decoded or stored, so the backup request is not a replay. Once any text has
arrived the request keeps the ordinary total budget and is never switched.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable

# Failures before any text that may move the same turn to the next provider. Blocked,
# invalid or unparseable output never does: a different model must not get a second
# chance to produce an action or an answer the first one was refused.
SWITCH_KINDS = frozenset({"timeout", "server_error", "connection_error", "rate_limit"})


class FirstTextTimeout(TimeoutError):
    """No text from the provider within llm.first_text_timeout_seconds."""


def first_text_seconds(settings) -> float:
    value = getattr(settings, "llm_first_text_timeout_seconds", 0)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


async def await_first_text(work: Awaitable, started: asyncio.Event, first_seconds: float, total_seconds: float):
    """Await work; FirstTextTimeout when `started` is still unset after first_seconds.

    first_seconds <= 0 (no backup, or disabled) keeps the plain total timeout. The
    abandoned request is cancelled and awaited, so its stream is closed before a backup starts.
    """
    loop = asyncio.get_running_loop()
    began = loop.time()
    task = asyncio.ensure_future(work)
    try:
        if 0 < first_seconds < total_seconds:
            await asyncio.wait({task}, timeout=first_seconds)
            if not task.done() and not started.is_set():
                raise FirstTextTimeout()
        return await asyncio.wait_for(task, timeout=max(0.0, total_seconds - (loop.time() - began)))
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
