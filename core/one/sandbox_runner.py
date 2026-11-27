"""Runs one skill she made, in its own `python -I` process (started by core/one/made.py).

Line protocol on stdin/stdout, JSON per line:
    in:  {"args": {...}}                         first line: the skill's arguments
    out: {"call": "<tool>", "args": {...}}       eva.call(...): one of her tools
    in:  {...}                                   that tool's answer
    out: {"done": true, "value": ...}  or  {"error": "..."}
print() inside a skill goes to stderr, never into the protocol.
"""

import builtins
import json
import sys

ALLOWED = {"json", "re", "math", "datetime", "statistics", "itertools", "collections", "functools", "random",
           "string", "textwrap", "time", "zoneinfo", "decimal", "fractions", "calendar", "difflib", "unicodedata",
           "urllib", "urllib.parse", "heapq", "bisect", "dataclasses", "typing", "_strptime", "_datetime"}

_out = sys.stdout
_in = sys.stdin
sys.stdout = sys.stderr
_real_import = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or (name not in ALLOWED and name.split(".")[0] not in {"json", "re", "math", "datetime", "collections",
                                                                    "typing", "zoneinfo", "decimal", "urllib"}):
        raise ImportError(f"import {name} isn't allowed in a skill")
    if name.startswith("urllib") and name not in ("urllib", "urllib.parse"):
        raise ImportError(f"import {name} isn't allowed in a skill")
    return _real_import(name, globals, locals, fromlist, level)


class Eva:
    """What a skill can do: call her tools."""

    def call(self, tool, **args):
        _out.write(json.dumps({"call": str(tool), "args": args}, default=str) + "\n")
        _out.flush()
        line = _in.readline()
        if not line:
            raise RuntimeError("E.V.A. stopped answering")
        return json.loads(line)


_EXEC = builtins.exec
compile_ = builtins.compile


def main():
    path = sys.argv[1]
    first = json.loads(_in.readline() or "{}")
    with builtins.open(path, encoding="utf-8") as f:
        code = f.read()
    safe = {k: v for k, v in vars(builtins).items()
            if k not in ("open", "exec", "eval", "compile", "input", "breakpoint", "globals", "locals", "vars",
                         "getattr", "setattr", "delattr", "memoryview", "exit", "quit", "help")}
    safe["__import__"] = _guarded_import                 # the skill's own builtins; the standard library keeps its own
    scope = {"__name__": "skill", "__builtins__": safe}
    _EXEC(compile_(code, path, "exec"), scope)
    value = scope["run"](Eva(), **(first.get("args") or {}))
    _out.write(json.dumps({"done": True, "value": value}, default=str) + "\n")
    _out.flush()


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:                       # noqa: BLE001 - everything goes back as an error line
        try:
            _out.write(json.dumps({"error": f"{type(e).__name__}: {e}"[:600]}) + "\n")
            _out.flush()
        except Exception:
            pass
