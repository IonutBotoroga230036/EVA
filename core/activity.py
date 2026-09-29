"""
When did you last talk to E.V.A.? (v0.3)

Background work that needs the GPU (FORGE's local coder) checks this and steps aside: your 6 GB card can't hold
the 14B coder and the conversation model at once, and swapping them made a simple question take a minute.
"""

from __future__ import annotations

import threading
import time

_last = 0.0
_seq = 0                                  # counts turns: exact on every OS (Windows' monotonic clock ticks ~15 ms)
_lock = threading.Lock()


def mark() -> None:
    """A turn started or finished."""
    global _last, _seq
    with _lock:
        _last = time.monotonic()
        _seq += 1


def token() -> int:
    """Take before starting work; busy_since(token) tells whether you talked since."""
    with _lock:
        return _seq


def since() -> float:
    with _lock:
        return time.monotonic() - _last if _last else 1e9


def busy_since(tok: int) -> bool:
    """Did a turn start or finish after token() returned tok?"""
    with _lock:
        return _seq > tok


def wait_quiet(quiet: float = 20.0, poll: float = 1.0, max_wait: float = 6 * 3600) -> None:
    """Block (in a worker thread) until nobody has talked to her for `quiet` seconds."""
    waited = 0.0
    while since() < quiet and waited < max_wait:
        time.sleep(poll)
        waited += poll
