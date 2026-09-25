"""
VAULT - Budget tracker. Tracks API spend per day AND per month, enforces both caps.

Prices are per 1,000 tokens in USD and can be overridden in config/settings.yaml
(budget.prices). Local models cost nothing: anything that isn't a Claude model is
recorded at EUR 0, so Ollama calls never eat the budget.

Sonnet 5 defaults to $3/$15 per million tokens. Public sources disagree on whether
the $2/$10 introductory rate is still current; for a hard cap it is better to
overestimate slightly than to overspend.
"""

import json
from datetime import date, datetime
from pathlib import Path

from loguru import logger

DEFAULT_PRICES = {                        # USD per 1K tokens: input, output
    "claude-haiku-4-5": {"input": 0.001, "output": 0.005},
    "claude-sonnet-5": {"input": 0.003, "output": 0.015},
    "claude-opus-5-5": {"input": 0.004, "output": 0.020},
    "claude-opus-5": {"input": 0.005, "output": 0.025},
    "claude-fable-5-1": {"input": 0.010, "output": 0.050},
}
USD_TO_EUR = 0.92


class BudgetExceeded(RuntimeError):
    pass


class BudgetTracker:
    def __init__(self, daily_limit_euros: float = 1.50, warning_threshold: float = 0.80,
                 track_file: str = "./data/budget.json", monthly_limit_euros: float = 50.0,
                 prices: dict | None = None):
        self.daily_limit = daily_limit_euros
        self.monthly_limit = monthly_limit_euros
        self.warning_threshold = warning_threshold
        self.prices = {**DEFAULT_PRICES, **(prices or {})}
        self.track_file = Path(track_file)
        self.track_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.track_file.exists():
            try:
                with open(self.track_file) as f:
                    return json.load(f)
            except Exception:
                logger.warning("VAULT: budget file unreadable, starting a fresh one")
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

    def price_for(self, model: str) -> dict:
        m = (model or "").lower()
        if "claude" not in m:
            return {"input": 0.0, "output": 0.0}          # local models are free
        for key in sorted(self.prices, key=len, reverse=True):
            if m.startswith(key):
                return self.prices[key]
        return self.prices["claude-sonnet-5"]            # unknown Claude model: assume mid-tier

    def estimate_eur(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.price_for(model)
        return ((input_tokens / 1000) * p["input"] + (output_tokens / 1000) * p["output"]) * USD_TO_EUR

    def record_usage(self, model: str, input_tokens: int, output_tokens: int, purpose: str = "") -> float:
        cost_eur = self.estimate_eur(model, input_tokens, output_tokens)
        day = self._ensure_today()
        day["total_euros"] += cost_eur
        day["calls"].append({"time": datetime.now().isoformat(), "model": model, "purpose": purpose,
                             "input_tokens": input_tokens, "output_tokens": output_tokens,
                             "cost_eur": round(cost_eur, 6)})
        self._save()
        if not self.can_spend():
            logger.warning(f"VAULT: budget reached (today EUR {day['total_euros']:.3f}, month "
                           f"EUR {self.month_spent():.2f}). Cloud calls paused; local still works.")
        elif day["total_euros"] >= self.daily_limit * self.warning_threshold:
            logger.warning(f"VAULT: daily budget at {day['total_euros'] / self.daily_limit * 100:.0f}%")
        return cost_eur

    def month_spent(self) -> float:
        prefix = self._today()[:7]
        return sum(d["total_euros"] for k, d in self._data["days"].items() if k.startswith(prefix))

    def can_spend(self, amount_eur: float = 0.0) -> bool:
        today = self._ensure_today()["total_euros"]
        return (today + amount_eur < self.daily_limit) and (self.month_spent() + amount_eur < self.monthly_limit)

    def remaining_euros(self) -> float:
        return max(0.0, min(self.daily_limit - self._ensure_today()["total_euros"],
                            self.monthly_limit - self.month_spent()))

    def today_summary(self) -> dict:
        day = self._ensure_today()
        return {"spent": round(day["total_euros"], 4), "limit": self.daily_limit,
                "month_spent": round(self.month_spent(), 4), "month_limit": self.monthly_limit,
                "remaining": round(self.remaining_euros(), 4), "num_calls": len(day["calls"]),
                "exhausted": not self.can_spend()}


_budget: BudgetTracker | None = None


def get_budget() -> BudgetTracker:
    global _budget
    if _budget is None:
        from core.settings import get_settings
        b = get_settings().get("budget", {})
        _budget = BudgetTracker(daily_limit_euros=float(b.get("daily_limit_euros", 1.5)),
                                warning_threshold=float(b.get("warning_threshold", 0.8)),
                                track_file=b.get("track_file", "./data/budget.json"),
                                monthly_limit_euros=float(b.get("monthly_limit_euros", 50.0)),
                                prices=b.get("prices"))
    return _budget
