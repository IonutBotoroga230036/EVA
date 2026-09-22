"""
PULSE - Redis-backed event bus for inter-module communication.
"""

import json
import threading
from typing import Callable
import redis
from loguru import logger


class PulseEventBus:
    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379):
        self._redis = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        self._handlers: dict[str, list[Callable]] = {}
        self._listener_thread: threading.Thread | None = None
        self._running = False

    def publish(self, channel: str, data: dict) -> None:
        message = json.dumps(data)
        self._redis.publish(f"eva:{channel}", message)
        logger.debug(f"PULSE -> {channel}: {data.get('event', 'unknown')}")

    def subscribe(self, channel: str, handler: Callable[[dict], None]) -> None:
        full_channel = f"eva:{channel}"
        if full_channel not in self._handlers:
            self._handlers[full_channel] = []
            self._pubsub.subscribe(full_channel)
        self._handlers[full_channel].append(handler)
        logger.info(f"PULSE: Subscribed to {channel}")

    def start_listening(self) -> None:
        if self._running:
            return
        self._running = True
        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()
        logger.info("PULSE: Event bus listening")

    def _listen(self) -> None:
        for message in self._pubsub.listen():
            if not self._running:
                break
            if message["type"] != "message":
                continue
            channel = message["channel"]
            try:
                data = json.loads(message["data"])
            except json.JSONDecodeError:
                continue
            for handler in self._handlers.get(channel, []):
                try:
                    handler(data)
                except Exception as e:
                    logger.error(f"PULSE handler error on {channel}: {e}")

    def stop(self) -> None:
        self._running = False
        self._pubsub.unsubscribe()
        logger.info("PULSE: Event bus stopped")