"""Test mode (Nov 23): run docs/VOICE_TESTS.md without spending real money.

"Eva, test mode on": from now, cloud calls may spend at most a small cap (10 cents unless you say another amount,
"test mode on with 50 cents"). Past the cap every cloud feature falls back to the local model, the same way it
does when the daily budget is used up; FORGE builds too (each Claude call checks first). Each turn's cost is
written to the log ("COST turn: ..."), free turns too. "Test mode off" says what the session cost. It ends by
itself 12 hours after you started it (Nov 24: it used to end at midnight, 3 minutes after you turned it on).
State: data/test_mode.json.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

PATH = Path("data/test_mode.json")
DEFAULT_CAP = 0.10
HOURS = 12


def _budget():
    from core.budget import get_budget
    return get_budget()


def _raw() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def _since_calls(since: float) -> tuple[float, int]:
    """(euros, cloud calls) recorded since a moment, across midnight."""
    eur, n = 0.0, 0
    for day in _budget()._data["days"].values():
        for c in day.get("calls", []):
            try:
                if datetime.fromisoformat(c["time"]).timestamp() >= since:
                    eur += float(c.get("cost_eur", 0.0))
                    n += 1
            except (KeyError, ValueError):
                continue
    return eur, n


def state() -> dict:
    st = _raw()
    since = float(st.get("since", 0.0))
    expired = bool(st.get("on")) and time.time() - since > HOURS * 3600
    return {"on": bool(st.get("on")) and not expired, "expired": expired, "cap": float(st.get("cap", DEFAULT_CAP)),
            "since": since}


def is_on() -> bool:
    return state()["on"]


def spent() -> tuple[float, int]:
    """(euros, cloud calls) since test mode started (also once it ended by itself, until you switch it off)."""
    st = state()
    if not (st["on"] or st["expired"]):
        return 0.0, 0
    return _since_calls(st["since"])


def blocks(amount: float = 0.0) -> bool:
    """True when test mode is on and this would go past its cap (core/budget.py asks before each cloud call)."""
    st = state()
    if not st["on"]:
        return False
    return spent()[0] + float(amount or 0.0) >= st["cap"]


def set_mode(on: bool, cap: Optional[float] = None) -> dict:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    st = {"on": True, "cap": float(cap if cap else DEFAULT_CAP), "since": time.time()} if on else {"on": False}
    PATH.write_text(json.dumps(st), encoding="utf-8")
    return state()


def money(eur: float) -> str:
    if eur < 1:
        cents = eur * 100
        return f"{cents:.0f} cents" if cents >= 1 or cents == 0 else f"{cents:.2f} cents"
    return f"{eur:.2f} euros"


def tool_test_mode(on: bool = True, cap_euros: float = 0.0, **_):
    if on:
        try:
            cap = float(cap_euros or 0) or None
        except (TypeError, ValueError):
            cap = None
        st = set_mode(True, cap)
        return {"result": json.dumps({"test_mode": True, "cap_eur": st["cap"]}), "exact": True,
                "say": f"Test mode on, sir. Cloud calls can spend {money(st['cap'])} in total now; after that I "
                       f"use the local model. I'll write what each turn costs in the log."}
    eur, calls = spent()
    st = state()
    set_mode(False)
    if st["expired"]:
        return {"result": json.dumps({"test_mode": False, "spent_eur": round(eur, 5), "calls": calls}), "exact": True,
                "say": f"Test mode had already ended by itself after {HOURS} hours, sir. Since you started it: "
                       f"{money(eur)} in {calls} cloud call{'s' if calls != 1 else ''}."}
    if not st["on"]:
        return {"result": json.dumps({"test_mode": False}), "exact": True, "say": "Test mode was already off, sir."}
    return {"result": json.dumps({"test_mode": False, "spent_eur": round(eur, 5), "calls": calls}), "exact": True,
            "say": f"Test mode off, sir. The session cost {money(eur)} in {calls} cloud call{'s' if calls != 1 else ''}."}


_ON = re.compile(r"^(?:(?:hey )?eva[,.]?\s*)?(?:(?:turn|switch) on |start |enable |begin )?test(?:ing)? mode"
                 r"(?: on)?(?: please)?[.!]*$|^(?:(?:hey )?eva[,.]?\s*)?(?:let'?s )?(?:start )?testing[.!]*$", re.I)
_OFF = re.compile(r"^(?:(?:hey )?eva[,.]?\s*)?(?:(?:turn|switch) off |stop |end |disable |exit |leave )test(?:ing)? "
                  r"mode[.!]*$|^(?:(?:hey )?eva[,.]?\s*)?test(?:ing)? mode off[.!]*$", re.I)


def from_words(text: str) -> Optional[bool]:
    t = (text or "").strip()
    if _OFF.match(t):
        return False
    if _ON.match(t):
        return True
    return None


SCHEMAS = [{"type": "function", "function": {
    "name": "test_mode",
    "description": "Test mode on or off: while testing E.V.A., cap cloud spending at a few cents and log each turn's "
                   "cost ('test mode on', 'test mode off', 'test mode on with 50 cents').",
    "parameters": {"type": "object", "properties": {
        "on": {"type": "boolean"},
        "cap_euros": {"type": "number", "description": "Only if the user names an amount, in euros (50 cents = 0.5)."}},
        "required": ["on"]}}}]
FUNCTIONS = {"test_mode": tool_test_mode}
GUARDS = {"test_mode": r"\btest(?:ing)?\b"}
ACTIONS = {"test_mode"}
EXACT = {"test_mode"}
