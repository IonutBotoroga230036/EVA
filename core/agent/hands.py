"""What a task can do (v0.4): the agent's tools, each with its consent kind (core/agent/consent.py).

A hand returns one of:
    ("result", text, is_error)   the tool result Claude sees
    ("ask", consent_text)        it needs your yes first; the task waits (consent tier "ask")
    ("question", text)           ask_user: the task waits for your answer
    ("finish", summary, details) the task is done
A result may also carry a picture: ("result", [text block, image block], is_error), so Claude sees it.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from loguru import logger

from core.agent import consent, live
from core.agent.pc import PCError, get_pc

MAX_RESULT = 8000
# E.V.A. tools a task may use without asking: nothing personal, nothing that acts. Everything else asks first,
# because a task also reads web pages, and a page must never be able to make it read your mail and send it out.
FREE_EVA_TOOLS = {"get_datetime", "calculate", "weather", "get_weather", "train_times", "convert_currency",
                  "get_exchange_rate", "list_rates", "moon_phase", "get_moon_phase", "km_to_miles", "miles_to_km",
                  "project_note", "project_add_part", "project_show", "nearby"}     # v0.6: they only add to a project


def _s(props: dict, required=()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


STR, NUM, BOOL = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
SCHEMAS = [
    {"name": "update_plan", "description": "Write or rewrite the task's plan: a short list of steps. Do this first.",
     "input_schema": _s({"steps": {"type": "array", "items": STR}}, ["steps"])},
    {"name": "mark_step", "description": "Mark a plan step (0-based index) as done, with a one-line result.",
     "input_schema": _s({"index": {"type": "integer"}, "result": STR}, ["index"])},
    {"name": "ask_user", "description": "Ask the user a question and wait for the answer. Batch related questions "
     "into one; offer sensible defaults so a short answer works. Use it for real choices only.",
     "input_schema": _s({"question": STR}, ["question"])},
    {"name": "tell_user", "description": "A short progress update spoken to the user (milestones only, not every step).",
     "input_schema": _s({"message": STR}, ["message"])},
    {"name": "list_files", "description": "The files in this task's folder.", "input_schema": _s({})},
    {"name": "read_file", "description": "Read a text file from the task's folder.",
     "input_schema": _s({"path": STR}, ["path"])},
    {"name": "write_file", "description": "Write a text file in the task's folder (code, notes, an OpenSCAD .scad, "
     "a how-to .md). Paths are relative to the task folder.",
     "input_schema": _s({"path": STR, "content": STR}, ["path", "content"])},
    {"name": "run_python", "description": "Run Python code in the task's sandbox: the task folder is the working "
     "directory, no network, writes only inside the folder, packages from install_python_packages. Print what you "
     "need to see. Use it to generate files (e.g. an STL with numpy-stl or trimesh) and to check them.",
     "input_schema": _s({"code": STR, "timeout": NUM}, ["code"])},
    {"name": "install_python_packages", "description": "Install Python packages into E.V.A.'s own environment "
     "(not the user's). Asks the user first unless they gave a standing yes.",
     "input_schema": _s({"packages": {"type": "array", "items": STR}, "why": STR}, ["packages", "why"])},
    {"name": "check_program", "description": "Is a program installed on the user's PC (e.g. openscad, blender)?",
     "input_schema": _s({"name": STR}, ["name"])},
    {"name": "install_program", "description": "Install a program on the user's PC with winget (Windows). Always "
     "asks the user first. Prefer free, well-known programs; give the exact winget id.",
     "input_schema": _s({"winget_id": STR, "name": STR, "exe": STR, "why": STR}, ["winget_id", "name", "why"])},
    {"name": "run_program", "description": "Run an installed program on the task's files, with the task folder as "
     "working directory (e.g. openscad -o stand.stl stand.scad). Only relative paths inside the task folder.",
     "input_schema": _s({"program": STR, "args": {"type": "array", "items": STR}, "timeout": NUM},
                        ["program", "args"])},
    {"name": "web_search", "description": "Search the web (free). Returns titles, links and snippets.",
     "input_schema": _s({"query": STR}, ["query"])},
    {"name": "fetch_page", "description": "Read a public web page as text.",
     "input_schema": _s({"url": STR}, ["url"])},
    {"name": "find_places", "description": "Find places near the user (shops, services) with addresses, from "
     "OpenStreetMap and the web. 'near' defaults to where the user lives.",
     "input_schema": _s({"what": STR, "near": STR}, ["what"])},
    {"name": "list_eva_tools", "description": "Search E.V.A.'s everyday tools (calendar, reminders, notes, email, "
     "lights...) to use with use_eva_tool.", "input_schema": _s({"query": STR}, ["query"])},
    {"name": "use_eva_tool", "description": "Use one of E.V.A.'s everyday tools. Tools that change something "
     "(add to the calendar, send) ask the user first.",
     "input_schema": _s({"tool": STR, "args": {"type": "object"}}, ["tool"])},
    {"name": "preview_model", "description": "Draw a 3D model (STL) from the task folder and LOOK at it: a 3D view, "
     "the front and the side, with its size in mm. Use it on every model you make, and on an assembly of the parts "
     "in place, before you call it done. The user sees the same pictures.",
     "input_schema": _s({"path": STR}, ["path"])},
    {"name": "deliver_file", "description": "Copy finished files from the task folder to the user's E.V.A. "
     "folder (E.V.A. Files/<task>, next to her code), where they can open them. Give all of them in one call: one question "
     "for the user. Asks first unless they gave a standing yes.",
     "input_schema": _s({"paths": {"type": "array", "items": STR}, "path": STR})},
    {"name": "download_file", "description": "Download a public file (a model, weights, a dataset, an image) into "
     "the task folder. The sandbox has no network: download what your code needs first, then run it.",
     "input_schema": _s({"url": STR, "path": STR}, ["url"])},
    {"name": "find_my_files", "description": "Find files in the user's E.V.A. folder (what earlier tasks made, or "
     "what he put there): names, folders, sizes.", "input_schema": _s({"query": STR})},
    {"name": "import_file", "description": "Copy a file from the user's E.V.A. folder into this task's folder, to "
     "work on it (his script, his trained model, an earlier task's output).",
     "input_schema": _s({"path": STR, "as_name": STR}, ["path"])},
    {"name": "finish", "description": "The task is done. summary: one or two short spoken sentences (what you made "
     "and the one thing to do next), no lists. details: the full write-up for the screen (sizes, print settings, "
     "steps, caveats), markdown is fine. Check the files exist before you finish.",
     "input_schema": _s({"summary": STR, "details": STR}, ["summary"])},
]
KIND = {"update_plan": "look", "mark_step": "look", "ask_user": "look", "tell_user": "look", "list_files": "look",
        "read_file": "look", "write_file": "workspace", "run_python": "run_code",
        "install_python_packages": "pip_install", "check_program": "look", "install_program": "install_program",
        "run_program": "run_program", "web_search": "look", "fetch_page": "look", "find_places": "look",
        "list_eva_tools": "look", "use_eva_tool": "eva_action", "deliver_file": "export_file", "finish": "look",
        "preview_model": "look", "download_file": "look", "find_my_files": "look", "import_file": "workspace"}


def _json(d) -> str:
    t = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False, default=str)
    return t if len(t) <= MAX_RESULT else t[:MAX_RESULT] + "...(cut)"


def _paths(args: dict) -> list[str]:
    """deliver_file takes paths (a list) or one path."""
    out = [str(p) for p in (args.get("paths") or []) if str(p).strip()]
    if args.get("path") and str(args["path"]) not in out:
        out.append(str(args["path"]))
    return out[:30]


def _public_url(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except (OSError, ValueError):
        return False


def _html_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as h
    text = h.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


class Hands:
    """The tools of one task. `notify(kind, text)` tells the user (progress); the runner handles waiting."""

    def __init__(self, task, store, notify: Callable[[str, str], None], belt_factory: Optional[Callable] = None,
                 http=None, search: Optional[Callable] = None, home: str = ""):
        self.task, self.store, self.notify = task, store, notify
        self._belt_factory = belt_factory
        self.http, self._search = http, search
        self.home = home
        self._last_tell = 0.0

    # ---------------------------------------------------------------- consent
    def needs_yes(self, name: str, args: dict) -> Optional[str]:
        """The question to ask, or None when this call may go ahead now."""
        kind = KIND.get(name, "eva_action")
        if name == "use_eva_tool":
            tool = str(args.get("tool") or "")
            if tool in FREE_EVA_TOOLS:
                return None                                          # the time, a sum, the weather: free
            belt = self._belt()
            if tool in belt.actions or belt.needs_confirm(tool):
                return f"use {tool}: {belt.describe(tool, dict(args.get('args') or {}))}"
            return f"read with {tool} ({json.dumps(args.get('args') or {}, ensure_ascii=False)[:120]})"
        if name == "run_program":                     # programs it installed or you listed run freely; others ask
            from core.agent.pc import DEFAULT_PROGRAMS, _cfg, _known_programs
            allowed = {p.lower() for p in _cfg().get("programs", DEFAULT_PROGRAMS)} | set(_known_programs())
            prog = re.sub(r"\.exe$", "", str(args.get("program") or "").lower())
            granted = "run_program" in consent.standing() or "run_program" in (self.task.grants or {})
            if prog not in allowed and not granted:
                return f"run {args.get('program')} {' '.join(map(str, args.get('args') or []))} on the task's files"
            return None
        if consent.tier(kind, self.task.grants) != consent.ASK:
            return None
        if name == "install_program":
            return f"install {args.get('name') or args.get('winget_id')} on your PC (winget {args.get('winget_id')}). " \
                   f"{args.get('why', '')}".strip()
        if name == "install_python_packages":
            return f"install the Python packages {', '.join(map(str, args.get('packages') or []))} into my own " \
                   f"environment. {args.get('why', '')}".strip()
        if name == "deliver_file":
            names = _paths(args)
            what = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}" if names else "the files"
            return f"copy {what} to your E.V.A. folder ({self._out_folder()})"
        return f"{consent.describe(kind)}: {name}"

    def _out_folder(self) -> str:
        from core.agent.pc import _cfg
        from core.one import files as my
        return str(my.root() / self.task.title)

    def log_consent(self, kind: str, what: str, decision: str) -> None:
        self.task.consents.append({"ts": time.time(), "kind": kind, "what": what[:300], "decision": decision})

    # ---------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict, approved: bool = False):
        if name not in KIND:
            return ("result", f"there's no tool called {name}", True)
        if name == "ask_user":
            return ("question", str(args.get("question") or "").strip() or "How should I go on, sir?")
        if name == "finish":
            return ("finish", str(args.get("summary") or "").strip(), str(args.get("details") or "").strip())
        if not approved:
            q = self.needs_yes(name, args)
            if q:
                return ("ask", q)
        kind = KIND[name]
        if kind not in ("look",):
            self.log_consent(kind, f"{name} {_json(args)[:200]}", "approved" if approved else
                             ("standing" if kind in consent.standing() or kind in self.task.grants else "logged"))
        what = live.label(name, args)
        live.emit(self.task, "doing", label=what, tool=name)
        try:
            out = self._do(name, args, what)
            if isinstance(out, dict) and out.get("_image_b64"):             # Claude sees the picture too
                img = out.pop("_image_b64")
                return ("result", [{"type": "text", "text": _json(out)},
                                   {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                "data": img}}], False)
            return ("result", _json(out), False)
        except (PCError, ValueError, TypeError, KeyError) as e:
            return ("result", f"error: {e}", True)
        except Exception as e:                                       # a tool crash is a result, not a dead task
            logger.exception(f"AGENT: {name} crashed")
            return ("result", f"error: {e.__class__.__name__}: {e}", True)

    def _do(self, name: str, args: dict, what: str):
        out = getattr(self, "h_" + name)(**args)
        bad = isinstance(out, dict) and (out.get("ok") is False or (isinstance(out.get("exit"), int) and out["exit"] != 0))
        live.emit(self.task, "did", label=what, tool=name, ok=not bad)
        return out

    # ---------------------------------------------------------------- the hands
    def h_update_plan(self, steps: list):
        self.task.plan = [{"step": str(s)[:200], "done": False, "result": ""} for s in (steps or [])[:20]]
        self.task.note(f"plan: {len(self.task.plan)} steps")
        live.emit(self.task, "plan")
        return {"ok": True, "steps": len(self.task.plan)}

    def h_mark_step(self, index: int, result: str = ""):
        i = int(index)
        if not 0 <= i < len(self.task.plan):
            raise ValueError(f"there's no step {i}")
        self.task.plan[i].update(done=True, result=str(result)[:200])
        self.task.note(f"done: {self.task.plan[i]['step']}")
        live.emit(self.task, "plan")
        return {"ok": True}

    def h_tell_user(self, message: str):
        self.task.note(f"told you: {message}")
        if time.time() - self._last_tell >= 90:
            self._last_tell = time.time()
            self.notify("progress", message)
            return {"told": True}
        return {"told": False, "note": "logged; updates are limited to one every 90 seconds"}

    def h_list_files(self):
        return get_pc().list_files(self.task.id)

    def h_read_file(self, path: str):
        return get_pc().read_file(self.task.id, path)

    def h_write_file(self, path: str, content: str):
        out = get_pc().write_file(self.task.id, path, content)
        self.task.note(f"wrote {out['path']}")
        live.emit(self.task, "code", path=out["path"], text=content)
        return out

    def h_run_python(self, code: str, timeout: float = 120):
        live.emit(self.task, "code", path="the code she's running", text=code)
        out = get_pc().run_python(self.task.id, code, int(timeout or 120))
        self.task.note(f"ran Python (exit {out.get('exit')})")
        live.emit(self.task, "output", text=(out.get("stdout") or "") + (out.get("stderr") or ""),
                  ok=out.get("exit") == 0)
        return out

    def h_install_python_packages(self, packages: list, why: str = ""):
        out = get_pc().pip_install(list(packages or []))
        self.task.note(f"pip install {' '.join(map(str, packages or []))}: {'ok' if out.get('ok') else 'failed'}")
        return out

    def h_check_program(self, name: str):
        return get_pc().find_program(name)

    def h_install_program(self, winget_id: str, name: str = "", exe: str = "", why: str = ""):
        out = get_pc().install_program(winget_id, exe)
        self.task.note(f"installed {name or winget_id}: {'ok' if out.get('ok') else 'failed'}")
        return out

    def h_run_program(self, program: str, args: list, timeout: float = 300):
        out = get_pc().run_program(self.task.id, program, list(args or []), int(timeout or 300))
        self.task.note(f"ran {program} (exit {out.get('exit')})")
        live.emit(self.task, "output", text=(out.get("stdout") or "") + (out.get("stderr") or ""),
                  ok=out.get("exit") == 0)
        return out

    def h_web_search(self, query: str):
        if self._search:
            return self._search(query)
        from core.tools_native import tool_web_search
        return json.loads(tool_web_search(query=query)["result"])

    def h_fetch_page(self, url: str):
        if not _public_url(url):
            raise ValueError("only public http(s) pages")
        import httpx
        http = self.http or httpx
        for _ in range(4):                                   # redirects are checked too: never into your network
            r = http.get(url, timeout=10.0, follow_redirects=False, headers={"User-Agent": "E.V.A. assistant"})
            loc = r.headers.get("location") if 300 <= r.status_code < 400 else None
            if not loc:
                break
            from urllib.parse import urljoin
            url = urljoin(url, loc)
            if not _public_url(url):
                raise ValueError("that page redirects to a private address")
        if r.status_code >= 400:
            return {"status": r.status_code}
        text = _html_text(r.text) if "html" in r.headers.get("content-type", "html") else r.text
        return {"url": url, "text": text[:7000]}

    def h_find_places(self, what: str, near: str = ""):
        near = near or self.home or "Breda, Netherlands"
        out: dict = {"near": near, "openstreetmap": [], "web": []}
        try:
            import httpx
            http = self.http or httpx
            r = http.get("https://nominatim.openstreetmap.org/search", timeout=10.0,
                         params={"q": f"{what}, {near}", "format": "jsonv2", "limit": 8, "addressdetails": 0},
                         headers={"User-Agent": "E.V.A. personal assistant (single user)"})
            if r.status_code == 200:
                out["openstreetmap"] = [{"name": p.get("name") or p.get("display_name", "").split(",")[0],
                                         "address": p.get("display_name"), "type": p.get("type")}
                                        for p in r.json()[:8]]
        except Exception as e:
            out["openstreetmap_error"] = str(e)[:120]
        try:
            out["web"] = self.h_web_search(f"{what} {near}").get("results", [])[:6]
        except Exception as e:
            out["web_error"] = str(e)[:120]
        return out

    def _belt(self):
        if self._belt_factory:
            return self._belt_factory()
        from core.orchestrator_hybrid import ToolBelt
        from skills.registry import get_registry
        return ToolBelt(get_registry(), None)

    def h_list_eva_tools(self, query: str):
        belt = self._belt()
        words = set(re.findall(r"[a-z]{3,}", (query or "").lower()))
        found = []
        for t in belt.schemas:
            f = t["function"]
            hay = f"{f['name']} {f.get('description', '')}".lower()
            score = sum(w in hay for w in words)
            if score and not f["name"].startswith(("task_", "forge_", "permission")):
                found.append((score, {"tool": f["name"], "description": f.get("description", "")[:200],
                                      "params": list((f.get("parameters") or {}).get("properties", {}))}))
        return {"tools": [x for _, x in sorted(found, key=lambda s: -s[0])[:8]]}

    def h_use_eva_tool(self, tool: str, args: Optional[dict] = None):
        if tool.startswith(("task_", "forge_", "permission")):
            raise ValueError("a task can't start tasks, build skills or change permissions")
        belt = self._belt()
        if not belt.has(tool):
            raise ValueError(f"E.V.A. has no tool called {tool}")
        out = belt.execute(tool, dict(args or {}))
        return {"result": out.get("result"), "say": out.get("say", "")}

    def h_preview_model(self, path: str):
        out = get_pc().preview(self.task.id, path)
        self.task.note(f"looked at {path}")
        live.emit(self.task, "model", path=out["model"], png=out["png"], size_mm=out["size_mm"])
        size = " x ".join(f"{x:g}" for x in out["size_mm"])
        return {"model": out["model"], "size_mm": size, "triangles": out["triangles"],
                "look": "Check: is it the shape you meant, the right size, stable, printable without supports?",
                "_image_b64": out["png_b64"]}

    def h_download_file(self, url: str, path: str = ""):
        out = get_pc().download(self.task.id, url, path)
        self.task.note(f"downloaded {out.get('saved')} ({out.get('bytes', 0) // 1024} KB)")
        return out

    def h_find_my_files(self, query: str = ""):
        from core.one import files as my
        return {"folder": str(my.root()), "files": my.find(query, 40)}

    def h_import_file(self, path: str, as_name: str = ""):
        out = get_pc().import_file(self.task.id, path, as_name)
        self.task.note(f"brought in {out.get('to')}")
        return out

    def h_deliver_file(self, paths: Optional[list] = None, path: str = ""):
        done = []
        for p in _paths({"paths": paths, "path": path}):
            out = get_pc().export_file(self.task.id, p, self.task.title)
            if out["copied_to"] not in self.task.files:
                self.task.files.append(out["copied_to"])
            self.task.note(f"delivered {p}")
            done.append(out["copied_to"])
        if not done:
            raise ValueError("give the files to deliver in paths")
        return {"copied_to": done}
