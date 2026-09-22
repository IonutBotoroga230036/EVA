"""
VAULT - Budget tracker. Tracks API spend per day, enforces caps.
"""

import json
from datetime import date, datetime
from pathlib import Path
from loguru import logger

MODEL_COSTS = {
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
    "claude-sonnet-4-6-20260901": {"input": 0.003, "output": 0.015},
}
USD_TO_EUR = 0.92


class BudgetTracker:
    def __init__(self, daily_limit_euros: float = 1.50, warning_threshold: float = 0.80, track_file: str = "./data/budget.json"):
        self.daily_limit = daily_limit_euros
        self.warning_threshold = warning_threshold
        self.track_file = Path(track_file)
        self.track_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.track_file.exists():
            with open(self.track_file) as f:
                return json.load(f)
        return {"days": {}}

    def _save(self) -> None:
        with open(self.track_file, "w") as f:
            json.dump(self._data, f, indent=2)

    def _today(self) -> str:
        return date.today().isoformat()

    def _ensure_today(self) -> dict:
        today = self._today()
        if today not in self._data["days"]:
            self._data["days"][today] = {"total_euros": 0.0, "calls": []}
        return self._data["days"][today]

    def record_usage(self, model: str, input_tokens: int, output_tokens: int) -> float:
        costs = MODEL_COSTS.get(model, {"input": 0.003, "output": 0.015})
        cost_usd = (input_tokens / 1000) * costs["input"] + (output_tokens / 1000) * costs["output"]
        cost_eur = cost_usd * USD_TO_EUR
        day_data = self._ensure_today()
        day_data["total_euros"] += cost_eur
        day_data["calls"].append({
            "time": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_eur": round(cost_eur, 6),
        })
        self._save()
        spent = day_data["total_euros"]
        if spent >= self.daily_limit:
            logger.warning(f"VAULT: Daily budget EXHAUSTED ({spent:.4f}/{self.daily_limit}). Local-only mode.")
        elif spent >= self.daily_limit * self.warning_threshold:
            logger.warning(f"VAULT: Budget at {spent / self.daily_limit * 100:.0f}%")
        return cost_eur

    def can_spend(self) -> bool:
        return self._ensure_today()["total_euros"] < self.daily_limit

    def remaining_euros(self) -> float:
        return max(0, self.daily_limit - self._ensure_today()["total_euros"])

    def today_summary(self) -> dict:
        day_data = self._ensure_today()
        return {
            "spent": round(day_data["total_euros"], 4),
            "limit": self.daily_limit,
            "remaining": round(self.remaining_euros(), 4),
            "num_calls": len(day_data["calls"]),
            "exhausted": not self.can_spend(),
        }