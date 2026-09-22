"""
AEGIS - Audit Log: Tamper-evident append-only log.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from loguru import logger


class AuditLog:
    def __init__(self, log_dir: str = "./data/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._last_hash = "GENESIS"

    def _today_file(self) -> Path:
        return self.log_dir / f"audit_{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    def log(self, action: str, subsystem: str, details: dict | None = None, sensitive: bool = False) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "subsystem": subsystem,
            "details": {} if sensitive else (details or {}),
            "sensitive": sensitive,
            "prev_hash": self._last_hash,
        }
        entry_json = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(entry_json.encode()).hexdigest()[:16]
        entry["hash"] = entry_hash
        self._last_hash = entry_hash
        with open(self._today_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
        if not sensitive:
            logger.debug(f"AEGIS AUDIT [{subsystem}]: {action}")


audit = AuditLog()