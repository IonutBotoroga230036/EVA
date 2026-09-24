"""
PULSE - in-process event bus (v0.2).

Replaces the Redis pub/sub version. E.V.A. is a single-machine monolith, so a
network broker only added a service to install, keep running, and debug
(Redis does not run natively on Windows). This bus keeps the same public
interface, so the legacy v0.1 orchestrator still works unchanged:

    bus = PulseEventBus(redis_host=..., redis_port=...)   # args accepted, ignored
    bus.subscribe("tool.executed", handler)
    bus.publish("tool.executed", {"event": "tool.executed", "tool": "get_weather"})
    bus.start_listening(); bus.stop()

Handlers can be plain functions or async functions. Subscribe to "*" to see
every event. A short ring buffer keeps recent events for the status endpoint.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import defaultdict, deque
from typing import Any, Callable

from loguru import logger


class PulseEventBus:
    def __init__(self, redis_host: str | None = None, redis_port: int | None = None,
                 history: int = 200, **_ignored: Any):
        self._handlers: dict[str, list[Callable[[dict], Any]]] = defaultdict(list)
        self._recent: deque[dict] = deque(maxlen=history)
        self._running = True
        self._tasks: set[asyncio.Task] = set()

    # --- subscription -------------------------------------------------------
    def subscribe(self, channel: str, handler: Callable[[dict], Any]) -> None:
        self._handlers[channel].append(handler)
        logger.debug(f"PULSE: subscribed to {channel}")

    def unsubscribe(self, channel: str, handler: Callable[[dict], Any]) -> None:
        if handler in self._handlers.get(channel, []):
            self._handlers[channel].remove(handler)

    # --- publishing ---------------------------------------------------------
    def publish(self, channel: str, data: dict | None = None) -> None:
        if not self._running:
            return
        data = dict(data or {})
        data.setdefault("event", channel)
        data.setdefault("ts", time.time())
        self._recent.append({"channel": channel, **data})
        for handler in list(self._handlers.get(channel, [])) + list(self._handlers.get("*", [])):
            try:
                result = handler(data)
                if inspect.isawaitable(result):
                    self._schedule(result)
            except Exception as e:  # a broken handler must never break the publisher
                logger.error(f"PULSE handler error on {channel}: {e}")

    def _schedule(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)          # no loop running (scripts, tests): run inline
            return
        task = loop.create_task(coro)
        self._tasks.add(task)          # keep a reference so it isn't garbage collected
        task.add_done_callback(self._tasks.discard)

    # --- introspection & legacy compatibility -------------------------------
    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._recent)[-limit:]

    def start_listening(self) -> None:
        self._running = True
        logger.info("PULSE: in-process event bus online")

    def stop(self) -> None:
        self._running = False
        logger.info("PULSE: event bus stopped")


_bus: PulseEventBus | None = None


def get_bus() -> PulseEventBus:
    """Process-wide bus shared by the server, orchestrators, and skills."""
    global _bus
    if _bus is None:
        _bus = PulseEventBus()
    return _bus
