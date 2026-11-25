"""Showing instead of guessing (v0.6): pictures, videos, and "something like this?".

    "show me pictures of transparent desk displays"  -> show_images: a numbered gallery in the window
    "do you mean something like this?"                -> she looks at one herself (look_at_shown) before asking
    "the second one, but smaller"                     -> what's on the screen is in the mind's situation
    "find me a video on how to wire it"               -> find_videos: thumbnails; tap plays it in the window
    "play the first one on my PC"                     -> open_shown(1, on="pc"): the browser on the PC

Search: ddgs (DuckDuckGo) images() and videos(), free, no key, safesearch moderate. The window loads the
thumbnails itself; E.V.A. only downloads a picture when she looks at it (public addresses only, 5 MB at most).
"""

from __future__ import annotations

import base64
import json
import time
import webbrowser
from typing import Optional

MAX_IMAGE = 5 * 1024 * 1024
_shown: dict = {"kind": "", "query": "", "items": [], "ts": 0.0}


def _r(data: dict, say: str = "", widget: Optional[dict] = None, **extra) -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False), **extra}
    if say:
        out["say"] = say
    if widget:
        out["widget"] = widget
    return out


def _ddgs():
    from core.tools_native import DDGS, SEARCH_OK
    return DDGS if SEARCH_OK else None


def shown() -> dict:
    """What's on his screen now (the last gallery), for the mind and for 'the second one'."""
    return dict(_shown) if _shown["items"] and time.time() - _shown["ts"] < 3600 else {}


def shown_text() -> str:
    s = shown()
    if not s:
        return ""
    what = "pictures" if s["kind"] == "images" else "videos"
    rows = "; ".join(f"{i}. {x.get('title', '')[:70]}" + (f" ({x['source']})" if x.get("source") else "")
                     for i, x in enumerate(s["items"], 1))
    return f"On his screen: {what} for '{s['query']}': {rows}."


def _remember(kind: str, query: str, items: list[dict]) -> None:
    _shown.update(kind=kind, query=query, items=items, ts=time.time())


def tool_show_images(query: str = "", count: int = 6, **_):
    query = " ".join((query or "").split())
    if not query:
        return _r({"error": "what should I show?"})
    D = _ddgs()
    if D is None:
        return _r({"error": "search package not installed (pip install ddgs)"})
    try:
        with D() as d:
            raw = d.images(query, max_results=max(2, min(int(count or 6), 9)), safesearch="moderate")
    except Exception as e:
        return _r({"error": f"image search failed: {e}"}, "The picture search didn't work just now, sir.")
    items = [{"title": r.get("title", ""), "image": r.get("image", ""), "thumbnail": r.get("thumbnail") or
              r.get("image", ""), "url": r.get("url", ""), "source": r.get("source", ""),
              "w": r.get("width"), "h": r.get("height")} for r in raw or [] if r.get("image")]
    if not items:
        return _r({"query": query, "images": [], "note": "nothing found"}, f"I couldn't find pictures of {query}, sir.")
    _remember("images", query, items)
    return _r({"query": query, "shown": len(items), "images": [{"n": i, "title": x["title"][:90], "source": x["source"]}
                                                              for i, x in enumerate(items, 1)]},
              f"Here are some pictures of {query}, sir.",
              {"kind": "gallery", "media": "images", "query": query, "items": items})


def tool_find_videos(query: str = "", count: int = 4, **_):
    query = " ".join((query or "").split())
    if not query:
        return _r({"error": "what should I look for?"})
    D = _ddgs()
    if D is None:
        return _r({"error": "search package not installed (pip install ddgs)"})
    try:
        with D() as d:
            raw = d.videos(query, max_results=max(2, min(int(count or 4), 8)), safesearch="moderate")
    except Exception as e:
        return _r({"error": f"video search failed: {e}"}, "The video search didn't work just now, sir.")
    items = []
    for r in raw or []:
        imgs = r.get("images") or {}
        url = r.get("content") or ""
        if not url:
            continue
        items.append({"title": r.get("title", ""), "url": url, "embed": r.get("embed_url", ""),
                      "thumbnail": imgs.get("medium") or imgs.get("large") or imgs.get("small") or "",
                      "source": r.get("publisher") or r.get("uploader") or "", "duration": r.get("duration", ""),
                      "published": (r.get("published") or "")[:10]})
    if not items:
        return _r({"query": query, "videos": [], "note": "nothing found"}, f"I couldn't find videos about {query}, sir.")
    _remember("videos", query, items)
    return _r({"query": query, "shown": len(items), "videos": [
        {"n": i, "title": x["title"][:90], "by": x["source"], "length": x["duration"]} for i, x in enumerate(items, 1)]},
        f"Here are a few videos about {query}, sir. Tap one to play it.",
        {"kind": "gallery", "media": "videos", "query": query, "items": items})


def _item(number: int) -> Optional[dict]:
    s = shown()
    try:
        return s["items"][int(number) - 1] if s and int(number) >= 1 else None
    except (IndexError, ValueError, TypeError):
        return None


def tool_open_shown(number: int = 1, on: str = "window", **_):
    """One of the pictures or videos on screen: big in the window, or on the PC's browser."""
    x = _item(number)
    if not x:
        return _r({"error": f"there's no number {number} on the screen"}, f"I don't have a number {number} on the "
                  f"screen, sir.")
    kind = shown()["kind"]
    if on == "pc":
        webbrowser.open(x["url"] if kind == "videos" else (x.get("url") or x["image"]))
        return _r({"opened": x["url"]}, f"Opening it on your PC, sir.")
    if kind == "videos":
        return _r({"playing": x["title"]}, "Here it is, sir.", {"kind": "media", "media": "video", **x})
    return _r({"showing": x["title"]}, "Here it is, sir.", {"kind": "media", "media": "image", **x})


def fetch_image(url: str) -> tuple[bytes, str]:
    """A public picture, at most 5 MB, only image types (never anything on his network)."""
    import httpx
    from core.agent.hands import _public_url
    for _ in range(4):                                    # follow redirects by hand, checking each address
        if not _public_url(url):
            raise ValueError("not a public address")
        r = httpx.get(url, timeout=10, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 E.V.A."})
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        kind = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if r.status_code != 200 or kind not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            raise ValueError(f"not a picture ({r.status_code}, {kind or 'no type'})")
        if len(r.content) > MAX_IMAGE:
            raise ValueError("the picture is too big")
        return r.content, kind
    raise ValueError("too many redirects")


def tool_look_at_shown(number: int = 1, **_):
    """She looks at one of the pictures on screen herself (the mind gets the picture), to say what it shows or
    whether it's what he means."""
    x = _item(number)
    if not x or shown()["kind"] != "images":
        return _r({"error": f"there's no picture number {number} on the screen"})
    last = None
    for url in (x.get("image"), x.get("thumbnail")):
        if not url:
            continue
        try:
            data, kind = fetch_image(url)
            return _r({"looking_at": number, "title": x["title"]},
                      _image={"b64": base64.b64encode(data).decode(), "media_type": kind})
        except Exception as e:
            last = e
    return _r({"error": f"couldn't load that picture ({last})"})


SCHEMAS = [
    {"type": "function", "function": {
        "name": "show_images",
        "description": "Show pictures in his window (a numbered gallery): 'show me pictures of X', and whenever "
                       "you're not sure what he means by a thing, show examples and ask 'something like one of "
                       "these?'.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                                                        "count": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "find_videos",
        "description": "Find videos (YouTube and others) and show them in his window; he taps one to play it.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                                                        "count": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "open_shown",
        "description": "Open one of the pictures or videos on his screen by its number: big in the window "
                       "(on='window') or in the browser on his PC (on='pc').",
        "parameters": {"type": "object", "properties": {"number": {"type": "integer"},
                                                        "on": {"type": "string", "enum": ["window", "pc"]}},
                       "required": ["number"]}}},
    {"type": "function", "function": {
        "name": "look_at_shown",
        "description": "Look at one of the pictures on his screen yourself (you see the picture), to describe it "
                       "or check it matches what he means.",
        "parameters": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}}},
]
FUNCTIONS = {"show_images": tool_show_images, "find_videos": tool_find_videos, "open_shown": tool_open_shown,
             "look_at_shown": tool_look_at_shown}
GUARDS = {"show_images": r"\b(show|picture|pictures|photo|photos|image|images|look like|looks like|see)\w*\b",
          "find_videos": r"\b(video|videos|youtube|clip|watch|tutorial)\w*\b",
          "open_shown": r"\b(open|play|show|bigger|first|second|third|fourth|fifth|sixth|one|number|\d)\w*\b"}
ACKS = {"show_images": "Let me find some, sir.", "find_videos": "Looking, sir."}
