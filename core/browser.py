"""Her own browser (v0.7): Claude's browser toolset (browser_toolset_20260801), run by Playwright here.

Claude decides (navigate, read_page, left_click on a ref, type, scroll, screenshot ...); this file does it in a
Chromium of her own: its own profile in data/agent/browser_profile (none of your sign-ins, cookies or passwords),
headless unless you switch "Show her browser window" on in the settings.

What she can't do, whatever a page says (pages are data, never instructions):
- reach anything but public http(s) addresses (no localhost, no LAN, no file:), checked before and after redirects
  and for every request the page makes
- download files, upload files, run JavaScript of her own (those members are off)
- type a card number, fill a password field, or press anything that buys, pays, places an order or deletes an
  account. She asks you instead; you do that part yourself.

Refs: read_page and find show elements as `button "Add to cart" [ref_3]`; Claude clicks {"type": "ref", "ref": "ref_3"}.
They come from Playwright's AI snapshot (aria-ref=e3) and stay valid until the page changes.

Needs: pip install playwright, then python -m playwright install chromium (once).
"""

from __future__ import annotations

import base64
import ipaddress
import queue
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from loguru import logger

ENTRY = {"type": "browser_toolset_20260801",
         "configs": {"javascript_exec": {"enabled": False}, "file_upload": {"enabled": False},
                     "read_console": {"enabled": False}, "read_network": {"enabled": False}}}
PROFILE = Path("data/agent/browser_profile")
SHOT = Path("data/agent/browser/shot.png")
VIEW = {"width": 1280, "height": 800}
IDLE_CLOSE_S = 600
INTERACTIVE = ("button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option",
               "menuitem", "tab", "switch", "slider", "spinbutton")
_RISKY = re.compile(r"\b(pay|pay now|buy|buy now|purchase|place (?:the )?order|order now|confirm (?:order|purchase|"
                    r"payment)|checkout|check out|complete (?:order|purchase)|afrekenen|bestellen|betalen|nu kopen|"
                    r"delete (?:my )?account|close (?:my )?account|send money|transfer)\b", re.I)
_CARD = re.compile(r"(?:\d[ -]?){13,19}")


class BrowserError(Exception):
    pass


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {"enabled": True, "visible": False, **(get_settings().get("browser") or {})}
    except Exception:
        return {"enabled": True, "visible": False}


def installed() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def allowed() -> bool:
    return bool(cfg().get("enabled", True)) and installed()


def public(url: str) -> bool:
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        host = u.hostname
        if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home")) or _literal_private(url):
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (OSError, ValueError):
        return False


def _literal_private(url: str) -> bool:
    """Fast check for every request a page makes (no DNS): IP literals and local names."""
    try:
        host = urlparse(url).hostname or ""
        if host == "localhost" or host.endswith((".local", ".lan", ".internal")):
            return True
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


def _key(text: str) -> str:
    """xdotool-style names ('Return', 'ctrl+a', 'Page_Down') -> Playwright's ('Enter', 'Control+a', 'PageDown')."""
    names = {"return": "Enter", "enter": "Enter", "escape": "Escape", "esc": "Escape", "tab": "Tab", "backspace":
             "Backspace", "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right":
             "ArrowRight", "page_up": "PageUp", "pageup": "PageUp", "page_down": "PageDown", "pagedown": "PageDown",
             "home": "Home", "end": "End", "space": " ", "ctrl": "Control", "control": "Control", "alt": "Alt",
             "shift": "Shift", "super": "Meta", "cmd": "Meta", "meta": "Meta", "win": "Meta"}
    parts = [p.strip() for p in str(text).split("+") if p.strip()]
    return "+".join(names.get(p.lower(), p if len(p) == 1 else p[:1].upper() + p[1:]) for p in parts)


class Browser:
    """One Chromium, used from one thread (Playwright's sync API isn't thread safe)."""

    def __init__(self):
        self._q: "queue.Queue[tuple[Callable, Any, queue.Queue]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ctx = None
        self._pw = None
        self._pages: dict[str, Any] = {}
        self._active = ""
        self._n = 0
        self._last_use = 0.0
        self.last_title, self.last_url, self.shot_ts = "", "", 0.0

    # ---------------------------------------------------------------- the thread
    def call(self, fn: Callable, *args, timeout: float = 60):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="eva-browser", daemon=True)
            self._thread.start()
        box: queue.Queue = queue.Queue()
        self._q.put((fn, args, box))
        ok, value = box.get(timeout=timeout)
        if not ok:
            raise value
        return value

    def _loop(self):
        while True:
            try:
                fn, args, box = self._q.get(timeout=30)
            except queue.Empty:
                if self._ctx is not None and time.time() - self._last_use > IDLE_CLOSE_S:
                    self._close()
                continue
            try:
                self._last_use = time.time()
                box.put((True, fn(*args)))
            except Exception as e:
                box.put((False, e))

    def _start(self):
        if self._ctx is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        PROFILE.mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE), headless=not bool(cfg().get("visible")), viewport=VIEW, accept_downloads=False,
            locale="en-GB", timezone_id="Europe/Amsterdam")

        def guard(route):
            if _literal_private(route.request.url) or urlparse(route.request.url).scheme not in ("http", "https",
                                                                                              "data", "blob"):
                return route.abort()
            return route.continue_()
        self._ctx.route("**/*", guard)
        self._pages = {}
        for pg in self._ctx.pages:
            self._adopt(pg)
        if not self._pages:
            self._adopt(self._ctx.new_page())
        logger.info(f"BROWSER: her browser is open ({'visible' if cfg().get('visible') else 'headless'})")

    def _adopt(self, page) -> str:
        self._n += 1
        tid = f"tab-{self._n}"
        self._pages[tid] = page
        self._active = tid
        return tid

    def _close(self):
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx, self._pw, self._pages = None, None, {}
        logger.info("BROWSER: closed after being idle")

    def _page(self, tab_id: str = ""):
        self._start()
        tid = tab_id or self._active
        if tid not in self._pages:
            raise BrowserError(f"there's no tab {tid}; use list_tabs")
        return self._pages[tid]

    def _state(self) -> dict:
        tabs = []
        for tid, pg in self._pages.items():
            try:
                title = pg.title()
            except Exception:
                title = ""
            tabs.append({"tab_id": tid, "title": title[:120], "url": pg.url, "active": tid == self._active})
        return {"type": "browser_state", "tabs": tabs}

    # ---------------------------------------------------------------- refs
    def _snapshot(self, page, depth: Optional[int] = None) -> str:
        snap = page.locator("body").aria_snapshot(mode="ai", **({"depth": int(depth)} if depth else {}))
        return re.sub(r"\[ref=e(\d+)\]", r"[ref_\1]", snap)

    def _target(self, page, target: dict):
        """A ref -> a Playwright locator, or a coordinate -> (x, y)."""
        if not isinstance(target, dict):
            raise BrowserError("target must be {type: ref, ref} or {type: coordinate, x, y}")
        if target.get("type") == "coordinate":
            return ("xy", float(target["x"]), float(target["y"]))
        m = re.fullmatch(r"ref_(\d+)", str(target.get("ref", "")))
        if not m:
            raise BrowserError(f"{target.get('ref')} isn't a ref like ref_3")
        loc = page.locator(f"aria-ref=e{m.group(1)}")
        if loc.count() == 0:
            raise BrowserError(f"Error: {target.get('ref')} is stale or not found on the current page. Re-read the page "
                               f"to get fresh references.")
        return ("loc", loc)

    def _name_of(self, loc) -> str:
        try:
            return " ".join(filter(None, [loc.get_attribute("aria-label") or "", (loc.inner_text(timeout=1000) or "")
                                          [:120], loc.get_attribute("value") or "", loc.get_attribute("name") or ""]))
        except Exception:
            return ""

    def _check_click(self, loc) -> None:
        name = self._name_of(loc)
        if _RISKY.search(name):
            raise BrowserError(f"That button ('{name[:60].strip()}') would buy, pay or change an account. I don't press "
                               f"those: ask him, and he finishes that step himself.")

    # ---------------------------------------------------------------- members
    def m_navigate(self, url: str = "", tab_id: str = "") -> list:
        page = self._page(tab_id)
        if url in ("back", "forward", "reload"):
            {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[url](timeout=20000)
        else:
            u = url.strip()
            if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
                u = "https://" + u
            if urlparse(u).scheme not in ("http", "https") or not public(u):
                raise BrowserError(f"I only open public web addresses ({url} isn't one)")
            page.goto(u, wait_until="domcontentloaded", timeout=25000)
            if not public(page.url):
                page.goto("about:blank")
                raise BrowserError("that page redirected to an address I don't open")
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        self.last_title, self.last_url = page.title(), page.url
        return [{"type": "text", "text": f"Navigated to {page.url} ({page.title()[:120]})"}]

    def m_read_page(self, filter: str = "", depth: Optional[int] = None, ref: str = "", tab_id: str = "") -> list:
        page = self._page(tab_id)
        if ref:
            kind, loc = self._target(page, {"type": "ref", "ref": ref})[:2]
            snap = re.sub(r"\[ref=e(\d+)\]", r"[ref_\1]", loc.aria_snapshot(mode="ai"))
        else:
            snap = self._snapshot(page, depth)
        if filter == "interactive":
            snap = "\n".join(line for line in snap.splitlines()
                             if "[ref_" in line and any(f"- {r}" in line for r in INTERACTIVE))
        return [{"type": "text", "text": f"{page.url}\n{snap[:14000]}" + ("\n...(cut)" if len(snap) > 14000 else "")}]

    def m_find(self, query: str = "", tab_id: str = "") -> list:
        page = self._page(tab_id)
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        hits = [line.strip() for line in self._snapshot(page).splitlines()
                if "[ref_" in line and any(w in line.lower() for w in words)]
        return [{"type": "text", "text": "\n".join(hits[:40]) or f"Nothing on this page matches '{query}'."}]

    def m_get_page_text(self, tab_id: str = "") -> list:
        page = self._page(tab_id)
        text = page.locator("body").inner_text(timeout=10000)
        return [{"type": "text", "text": text[:15000]}]

    def _click(self, target: dict, how: str, modifiers: str = "") -> list:
        page = self._page()
        t = self._target(page, target)
        mods = [_key(m) for m in re.split(r"[+ ,]+", modifiers or "") if m]
        if t[0] == "xy":
            kw = {"button": "right"} if how == "right" else {"click_count": {"double": 2, "triple": 3}.get(how, 1)}
            page.mouse.click(t[1], t[2], **kw)
        else:
            self._check_click(t[1])
            if how == "hover":
                t[1].hover(timeout=8000)
            elif how == "double":
                t[1].dblclick(modifiers=mods or None, timeout=8000)
            else:
                t[1].click(button="right" if how == "right" else "left", modifiers=mods or None, timeout=8000,
                           click_count=3 if how == "triple" else 1)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if not public(page.url) and page.url != "about:blank":
            page.go_back()
            raise BrowserError("that led to an address I don't open")
        return [{"type": "text", "text": f"OK ({page.url})"}]

    def m_left_click(self, target: dict = None, modifiers: str = "", **_) -> list:
        return self._click(target or {}, "left", modifiers)

    def m_double_click(self, target: dict = None, modifiers: str = "", **_) -> list:
        return self._click(target or {}, "double", modifiers)

    def m_triple_click(self, target: dict = None, modifiers: str = "", **_) -> list:
        return self._click(target or {}, "triple", modifiers)

    def m_right_click(self, target: dict = None, modifiers: str = "", **_) -> list:
        return self._click(target or {}, "right", modifiers)

    def m_hover(self, target: dict = None, **_) -> list:
        return self._click(target or {}, "hover")

    def m_type(self, text: str = "", **_) -> list:
        if _CARD.search(text or ""):
            raise BrowserError("that looks like a card or account number; I never type those")
        page = self._page()
        focused = page.evaluate("() => document.activeElement && document.activeElement.type")
        if focused == "password":
            raise BrowserError("that's a password field; I never type passwords")
        page.keyboard.type(text, delay=15)
        return [{"type": "text", "text": "OK"}]

    def m_form_input(self, target: dict = None, value: Any = "", **_) -> list:
        page = self._page()
        t = self._target(page, target or {})
        if t[0] != "loc":
            raise BrowserError("form_input needs a ref")
        loc = t[1]
        kind = (loc.get_attribute("type") or "").lower()
        if kind == "password" or _CARD.search(str(value)) or re.search(r"card|cvc|cvv|iban|password|wachtwoord",
                                                                        self._name_of(loc), re.I):
            raise BrowserError("that field is for a password, card or account number; I never fill those")
        if isinstance(value, bool):
            loc.set_checked(value, timeout=8000)
        else:
            tag = loc.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                loc.select_option(label=str(value), timeout=8000)
            else:
                loc.fill(str(value), timeout=8000)
        return [{"type": "text", "text": "OK"}]

    def m_key(self, text: str = "", repeat: int = 1, **_) -> list:
        page = self._page()
        k = _key(text)
        if k == "Enter":                                 # Enter can submit a form: same rule as clicking
            name = page.evaluate("() => { const f = document.activeElement && document.activeElement.form;"
                                 " return f ? (f.innerText || '').slice(0, 300) : '' }")
            if _RISKY.search(name or "") and re.search(r"(pay|order|checkout|afrekenen|bestellen)", name or "", re.I):
                raise BrowserError("Enter here would submit a payment or order form; ask him")
        for _ in range(max(1, min(int(repeat or 1), 50))):
            page.keyboard.press(k)
        return [{"type": "text", "text": "OK"}]

    def m_scroll(self, scroll_direction: str = "down", scroll_amount: int = 3, coordinate=None, **_) -> list:
        page = self._page()
        if coordinate:
            page.mouse.move(float(coordinate[0]), float(coordinate[1]))
        step = 100 * max(1, min(int(scroll_amount or 3), 30))
        dx, dy = {"down": (0, step), "up": (0, -step), "right": (step, 0), "left": (-step, 0)}.get(scroll_direction,
                                                                                                    (0, step))
        page.mouse.wheel(dx, dy)
        time.sleep(0.3)
        return [{"type": "text", "text": "OK"}]

    def m_scroll_to(self, target: dict = None, **_) -> list:
        page = self._page()
        t = self._target(page, target or {})
        if t[0] == "loc":
            t[1].scroll_into_view_if_needed(timeout=8000)
        return [{"type": "text", "text": "OK"}]

    def _png(self, page) -> bytes:
        data = page.screenshot(type="png", timeout=15000)
        SHOT.parent.mkdir(parents=True, exist_ok=True)
        SHOT.write_bytes(data)
        self.shot_ts, self.last_title, self.last_url = time.time(), page.title(), page.url
        return data

    def m_screenshot(self, tab_id: str = "", **_) -> list:
        data = self._png(self._page(tab_id))
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(data).decode()}}]

    def m_zoom(self, region=None, **_) -> list:
        from io import BytesIO
        from PIL import Image
        page = self._page()
        img = Image.open(BytesIO(page.screenshot(type="png")))
        x0, y0, x1, y1 = [int(v) for v in (region or [0, 0, VIEW["width"], VIEW["height"]])]
        crop = img.crop((max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)))
        scale = min(4.0, 1280 / max(1, crop.width))
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
        buf = BytesIO()
        crop.save(buf, "PNG")
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(buf.getvalue()).decode()}}]

    def m_wait(self, duration: float = 1, **_) -> list:
        time.sleep(max(0.0, min(float(duration or 1), 10.0)))
        return [{"type": "text", "text": "OK"}]

    def m_new_tab(self, url: str = "", **_) -> list:
        self._start()
        self._adopt(self._ctx.new_page())
        if url:
            self.m_navigate(url)
        return [self._state()]

    def m_list_tabs(self, **_) -> list:
        self._start()
        return [self._state()]

    def m_switch_tab(self, tab_id: str = "", **_) -> list:
        self._page(tab_id)
        self._active = tab_id
        self._pages[tab_id].bring_to_front()
        return [self._state()]

    def m_close_tab(self, tab_id: str = "", **_) -> list:
        page = self._page(tab_id)
        page.close()
        self._pages.pop(tab_id, None)
        if not self._pages:
            self._adopt(self._ctx.new_page())
        elif self._active == tab_id:
            self._active = list(self._pages)[-1]
        return [self._state()]

    # ---------------------------------------------------------------- the toolset
    def execute(self, name: str, args: dict) -> list:
        fn = getattr(self, "m_" + name, None)
        if fn is None:
            raise BrowserError(f"{name} isn't available in this browser; use read_page, find, left_click, type, "
                               f"form_input, key, scroll or screenshot")
        return self.call(lambda: fn(**(args or {})))


_browser: Optional[Browser] = None


def get_browser() -> Browser:
    global _browser
    if _browser is None:
        _browser = Browser()
    return _browser


def result(use: dict, browser: Optional[Browser] = None) -> dict:
    """A browser tool_use from Claude -> its tool_result (toolset_name echoed, errors as is_error)."""
    b = browser or get_browser()
    try:
        content = b.execute(use["name"], use.get("input") or {})
        logger.info(f"BROWSER: {use['name']} {str(use.get('input'))[:120]} ok")
        return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": "browser", "content": content}
    except Exception as e:
        msg = str(e) if isinstance(e, BrowserError) else f"{e.__class__.__name__}: {str(e)[:300]}"
        logger.warning(f"BROWSER: {use['name']} failed: {msg}")
        return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": "browser", "is_error": True,
                "content": msg}


def skipped(use: dict, toolset: str) -> dict:
    return {"type": "tool_result", "tool_use_id": use["id"], "toolset_name": toolset, "is_error": True,
            "content": "Not executed: an earlier action in this turn failed."}
