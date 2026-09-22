"""
CORTEX - Session context manager.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionContext:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    persona: str = "eva"
    started_at: datetime = field(default_factory=datetime.now)
    messages: list[dict] = field(default_factory=list)
    active_skill: str | None = None
    metadata: dict = field(default_factory=dict)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })

    def get_recent_messages(self, limit: int = 20) -> list[dict]:
        return [{"role": m["role"], "content": m["content"]} for m in self.messages[-limit:]]

    def clear(self) -> None:
        self.messages.clear()
        self.active_skill = None