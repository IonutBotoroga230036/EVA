"""Shopping list tools. Exact spoken lines; clearing the whole list asks first."""

import json
import re

from core import shopping

TOOLS = [
    {"type": "function", "function": {"name": "shopping_add", "description": "Add one or more items to the shopping list.",
     "parameters": {"type": "object", "properties": {"items": {"type": "string", "description": "e.g. 'milk, eggs and bread'"}},
                    "required": ["items"]}}},
    {"type": "function", "function": {"name": "shopping_remove", "description": "Remove an item from the shopping list.",
     "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}}},
    {"type": "function", "function": {"name": "shopping_tick", "description": "Tick an item off the shopping list (bought).",
     "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}}},
    {"type": "function", "function": {"name": "shopping_list", "description": "Read the shopping list.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "shopping_clear_done", "description": "Remove the ticked-off items.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "shopping_clear_all", "description": "Empty the whole shopping list.",
     "parameters": {"type": "object", "properties": {}}}},
]


def _out(data: dict, say: str) -> dict:
    return {"result": json.dumps(data), "say": say, "exact": True,
            "widget": {"kind": "note", "title": "Shopping list", "text": shopping.spoken(shopping.items())}}


def _and(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else f"{', '.join(xs[:-1])} and {xs[-1]}"


def shopping_add(items: str = "", **_):
    names = shopping.split_items(items)
    if not names:
        return {"result": json.dumps({"error": "no items"}), "say": "What should I add, sir?"}
    added, already = shopping.add(names)
    parts = []
    if added:
        parts.append(f"Added {_and(added)} to your shopping list, sir.")
    if already:
        parts.append(f"{_and(already).capitalize()} {'was' if len(already) == 1 else 'were'} already on it.")
    return _out({"added": added, "already": already}, " ".join(parts))


def shopping_remove(item: str = "", **_):
    gone = shopping.remove(item)
    if not gone:
        return {"result": json.dumps({"error": f"{item} is not on the list"}), "say": f"{item} isn't on your shopping list, sir."}
    return _out({"removed": gone}, f"Took {gone} off your shopping list, sir.")


def shopping_tick(item: str = "", **_):
    got = shopping.set_done(item, True)
    if not got:
        return {"result": json.dumps({"error": f"{item} is not on the list"}), "say": f"{item} isn't on your shopping list, sir."}
    return _out({"ticked": got}, f"Ticked off {got}, sir.")


def shopping_list(**_):
    rows = shopping.items()
    return _out({"items": rows}, shopping.spoken(rows))


def shopping_clear_done(**_):
    n = shopping.clear(done_only=True)
    return _out({"cleared": n}, f"Removed {n} ticked item{'s' if n != 1 else ''}, sir." if n else "Nothing was ticked off, sir.")


def shopping_clear_all(**_):
    n = shopping.clear(done_only=False)
    return _out({"cleared": n}, f"Your shopping list is empty now, sir. {n} item{'s' if n != 1 else ''} removed.")


_ADD = re.compile(r"\b(?:add|put)\s+(.+?)\s+(?:to|on)\s+(?:my |the )?(?:shopping|grocery)\s+list", re.I)
_REMOVE = re.compile(r"\b(?:remove|take|delete|cross)\s+(.+?)\s+(?:from|off)\s+(?:of\s+)?(?:my |the )?(?:shopping|grocery)\s+list", re.I)


def _fill_add(args: dict, text: str) -> dict:
    m = _ADD.search(text or "")
    return {**args, "items": m.group(1)} if m else args      # the user's own words beat the model's


def _fill_remove(args: dict, text: str) -> dict:
    m = _REMOVE.search(text or "")
    return {**args, "item": m.group(1)} if m else args


FUNCTIONS = {f.__name__: f for f in (shopping_add, shopping_remove, shopping_tick, shopping_list,
                                     shopping_clear_done, shopping_clear_all)}
ACKS = {n: None for n in FUNCTIONS}
FILLERS = {"shopping_add": _fill_add, "shopping_remove": _fill_remove}
ACTIONS = ["shopping_add", "shopping_remove", "shopping_tick", "shopping_clear_done", "shopping_clear_all"]
CONFIRM = {"shopping_clear_all": "empty your whole shopping list"}
_G = r"\b(shopping|grocer(?:y|ies))\b"
GUARDS = {n: _G for n in FUNCTIONS}
GUARDS["shopping_tick"] = _G + r"|\b(tick|bought|got the|cross)\b"
