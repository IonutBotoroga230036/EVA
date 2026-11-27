"""Small file helpers (v1.1).

Nov 27 23:07: a task crashed on Windows with PermissionError in os.replace(task.json.tmp, task.json): another
reader (the window polling /api/tasks, or the virus scanner) had the file open for a moment. replace() now waits a
little and tries again before giving up.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace(tmp: Path | str, target: Path | str, tries: int = 8) -> None:
    last: Exception | None = None
    for i in range(tries):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as e:              # Windows: someone has the target open right now
            last = e
            time.sleep(0.05 * (i + 1))
    try:                                          # last resort: write over it in place
        Path(target).write_bytes(Path(tmp).read_bytes())
        Path(tmp).unlink(missing_ok=True)
    except OSError:
        raise last if last else OSError(f"couldn't replace {target}")
