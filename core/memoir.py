"""Your biography (v1.1): she listens to your stories and writes them down properly.

    "Eva, I want to tell you a story" / "record my story" / Settings > Biography > Start
        -> she listens: longer pauses are fine, she doesn't answer every sentence (a soft tone says she got it)
    you talk, as long as you like, with breaks
    "that's the end" / "stop recording"
        -> she writes a chapter in your own voice: a title, when and where it happened, the people in it, the story
           with your words cleaned of "uh"s but nothing invented, and two or three questions a biographer would ask
           you next time
    "pause the story" holds it (to ask her something); "carry on with the story" goes on
    "cancel the story" throws that recording away

Files (yours, on your PC): <E.V.A. folder>/Biography/
    chapters/<date> <title>.md   the written chapters
    sessions/<date_time>.md      exactly what you said, for every chapter
    Biography.md                 every chapter in the order things happened, with a contents list
    index.json                   titles, periods, people, places, open questions

The writer: memoir.writer = auto (Claude Sonnet when her brain may use the cloud, else the local model), cloud or
local. Nothing is written anywhere else, and her memory doesn't store your stories.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

STATE = Path("data/memoir_state.json")
DEFAULTS = {"dir": "", "writer": "auto", "patience_secs": 2.5, "max_utterance_secs": 180, "follow_secs": 120,
            "cloud_model": "claude-sonnet-5-5"}
_lock = threading.RLock()

STOP = re.compile(r"^\W*(?:(?:ok(?:ay)?|right|so|alright|eva)[,.]?\s+)*(?:that'?s (?:it|all|the end(?: of (?:it|the "
                  r"story))?)|the end|end of (?:the )?(?:story|chapter)|stop (?:the )?(?:recording|story|biography)|"
                  r"(?:i'?m |we'?re )?(?:done|finished)(?: (?:with )?(?:the|my|this) story)?|finish (?:the )?(?:story|"
                  r"chapter)|you can (?:write it(?: down)?|stop)(?: now)?|write it (?:down|up)(?: now)?)\W*$", re.I)
PAUSE = re.compile(r"^\W*(?:eva[,.]?\s+)?(?:pause (?:the )?(?:story|recording|biography)|hold on(?:,? (?:a|one) "
                   r"(?:sec(?:ond)?|minute|moment))?|wait a (?:sec(?:ond)?|minute|moment))\W*$", re.I)
CANCEL = re.compile(r"^\W*(?:eva[,.]?\s+)?(?:cancel (?:the |this )?(?:story|recording)|(?:delete|forget|scrap) "
                    r"(?:that|this|the) story|don'?t write (?:that|this|it) down)\W*$", re.I)
RESUME = re.compile(r"\b(?:carry on|continue|go on|back to|resume)\b.{0,20}\b(?:story|recording|biography)\b", re.I)


def cfg() -> dict:
    try:
        from core.settings import get_settings
        return {**DEFAULTS, **(get_settings().get("memoir") or {})}
    except Exception:
        return dict(DEFAULTS)


def folder() -> Path:
    c = cfg()
    if c.get("dir"):
        return Path(c["dir"]).expanduser()
    from core.one import files
    return files.root() / "Biography"


def _state() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")


def status() -> dict:
    st = _state()
    parts = st.get("parts") or []
    return {"active": bool(st.get("active")), "paused": bool(parts) and not st.get("active"), "parts": len(parts),
            "words": sum(len(p["text"].split()) for p in parts), "since": st.get("started"),
            "topic": st.get("topic", "")}


def listening() -> dict:
    """Slower listening while it records (voice/listen.py asks at every new utterance)."""
    if not _state().get("active"):
        return {}
    c = cfg()
    return {"patience_secs": float(c["patience_secs"]), "max_utterance_secs": float(c["max_utterance_secs"]),
            "follow_secs": float(c["follow_secs"])}


def start(topic: str = "") -> dict:
    with _lock:
        st = _state()
        if st.get("parts") and not st.get("active"):          # a paused one: go on with it
            st["active"] = True
            _save(st)
            return {"resumed": True, **status()}
        _save({"active": True, "started": time.time(), "topic": (topic or "")[:200], "parts": []})
    logger.info(f"MEMOIR: recording{' (' + topic[:60] + ')' if topic else ''}")
    return {"resumed": False, **status()}


def add(text: str) -> dict:
    text = " ".join((text or "").split())
    if not text:
        return status()
    with _lock:
        st = _state()
        st.setdefault("parts", []).append({"ts": time.time(), "text": text[:20000]})
        st["active"] = True
        st.setdefault("started", time.time())
        _save(st)
    return status()


def pause() -> dict:
    with _lock:
        st = _state()
        st["active"] = False
        _save(st)
    return status()


def cancel() -> dict:
    with _lock:
        n = len(_state().get("parts") or [])
        _save({})
    return {"cancelled": n}


# ---------------------------------------------------------------- writing it down
PROMPT = """You are writing Ionut's biography with him. Below is a story he just told you out loud, transcribed by
speech recognition (there may be misheard words and repetitions). Turn it into one chapter of his biography:

- In his own voice, first person, the way he tells it: keep his phrases and humour. Fix the "uh"s, false starts
  and repetitions; put it in a natural order.
- Keep every fact, name, place, date and feeling he gave. Invent nothing: no details, no dialogue, no feelings he
  didn't say. If a word looks misheard and you can't tell, keep it and mark it [?].
- Paragraphs; for a long story a few short ### subheadings.
Answer with JSON only:
{"title": "a short title", "period": "when it happened, in his words or as a year/range ('' if he didn't say)",
 "year": a number for sorting (best guess from what he said, 0 if no idea),
 "people": ["names or roles of the others in it (never him: he is the one telling it)"], "places": ["places"],
 "summary": "two sentences",
 "text": "the chapter in markdown", "questions": ["2 or 3 short questions a good biographer would ask him next"]}"""


def _writer() -> str:
    w = str(cfg().get("writer") or "auto")
    if w in ("cloud", "local"):
        return w
    try:
        from core import one
        if one.backend_choice() == "local":
            return "local"
        from core.claude import get_claude
        return "cloud" if get_claude().available() else "local"
    except Exception:
        return "local"


def _write_json(raw_text: str, topic: str) -> dict:
    user = (f"What he wanted to tell: {topic}\n\n" if topic else "") + f"His words:\n{raw_text}"
    how = _writer()
    out = ""
    if how == "cloud":
        from core.claude import get_claude
        r = get_claude().message(system=PROMPT, messages=[{"role": "user", "content": user}],
                                 model=cfg()["cloud_model"], max_tokens=min(16000, 1500 + len(raw_text) // 2),
                                 purpose="biography")
        out = r.get("text") or ""
    else:
        import httpx
        from core.one import cfg as one_cfg
        from core.settings import local_cfg
        loc = local_cfg()
        r = httpx.post(f"{loc['base_url']}/api/chat", timeout=600, json={
            "model": one_cfg().get("local_model") or loc["decision_model"], "stream": False, "think": False,
            "format": "json", "options": {"num_ctx": 16384, "temperature": 0.3},
            "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": user}]})
        out = (r.json().get("message") or {}).get("content") or ""
    m = re.search(r"\{.*\}", out, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    data["_writer"] = how
    return data


def _slug(s: str) -> str:
    return re.sub(r"[^\w\- ]", "", s or "").strip()[:60] or "story"


def _index() -> dict:
    try:
        return json.loads((folder() / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return {"chapters": []}


def chapters() -> list[dict]:
    return _index().get("chapters", [])


def stop(write: bool = True) -> dict:
    """Write the recording down as a chapter. -> {"title", "file", "words", "questions", "say"} or {"error"}."""
    with _lock:
        st = _state()
        parts = st.get("parts") or []
        _save({})
    if not parts:
        return {"error": "nothing recorded", "say": "There was nothing recorded yet, sir."}
    raw = "\n\n".join(p["text"] for p in parts)
    when = datetime.fromtimestamp(parts[0]["ts"])
    base = folder()
    (base / "sessions").mkdir(parents=True, exist_ok=True)
    (base / "chapters").mkdir(parents=True, exist_ok=True)
    session = base / "sessions" / f"{when:%Y-%m-%d_%H%M}.md"
    session.write_text(f"# What you told me, {when:%d %B %Y, %H:%M}\n\n" + "\n\n".join(
        f"[{datetime.fromtimestamp(p['ts']):%H:%M}] {p['text']}" for p in parts) + "\n", encoding="utf-8")
    if not write:
        return {"session": str(session), "say": "I kept your words, sir, but didn't write a chapter."}
    try:
        data = _write_json(raw, st.get("topic", ""))
    except Exception as e:
        logger.warning(f"MEMOIR: couldn't write the chapter ({e})")
        data = {}
    if not data.get("text"):
        return {"session": str(session), "error": "writer failed",
                "say": "I kept every word you said, sir, but I couldn't write the chapter just now. Say 'write my "
                       "last story' later and I'll try again."}
    entry = _render(data, raw, when, session)
    q = entry["questions"][:2]
    say = f"I wrote it down as \"{entry['title']}\", sir." + (f" Next time I'd love to hear: {q[0]}" if q else "")
    return {**entry, "say": say}


_ME = re.compile(r"^(?:i|me|myself|him|he|the narrator|ionu[tț](?:[- ]alberto)?(?: bo[tț]oroga)?)$", re.I)


def _render(data: dict, raw: str, when: datetime, session: Path, path: Optional[Path] = None) -> dict:
    """Write one chapter file and its index entry; rebuild Biography.md."""
    base = folder()
    title = str(data.get("title") or "A story")[:80]
    path = path or base / "chapters" / f"{when:%Y-%m-%d} {_slug(title)}.md"
    people = [str(x) for x in data.get("people") or [] if not _ME.match(str(x).strip())][:20]
    places = [str(x) for x in data.get("places") or []][:20]
    head = [f"# {title}", ""]
    meta = " · ".join(x for x in [str(data.get("period") or ""), f"told {when:%d %B %Y}"] if x)
    head.append(f"*{meta}*")
    if people or places:
        head.append("")
        head.append(" · ".join(x for x in [("People: " + ", ".join(people)) if people else "",
                                           ("Places: " + ", ".join(places)) if places else ""] if x))
    body = "\n".join(head) + "\n\n" + str(data["text"]).strip() + \
        f"\n\n---\n*From what you told me: sessions/{session.name}*\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    idx = _index()
    try:
        year = int(data.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    entry = {"title": title, "file": str(path), "session": str(session), "period": str(data.get("period") or ""),
             "year": year, "people": people, "places": places, "summary": str(data.get("summary") or "")[:400],
             "questions": [str(q) for q in data.get("questions") or []][:3], "told": when.isoformat(),
             "words": len(raw.split()), "writer": data.get("_writer", "")}
    idx["chapters"] = [c for c in idx.get("chapters", []) if c.get("file") != str(path)] + [entry]
    (base / "index.json").write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")
    compile_book()
    logger.info(f"MEMOIR: wrote '{title}' ({entry['words']} words, {entry['writer']})")
    return entry


def add_to_chapter(text: str, title: str = "") -> dict:
    """v1.2: his answer to her follow-up question, or more of a story he just told (Nov 28 00:59: "I'll add this
    to The Empty Book" and nothing was added). His words go into the chapter's session; the chapter is rewritten."""
    text = " ".join((text or "").split())
    if not text:
        return {"error": "nothing to add", "say": "What should I add, sir?"}
    chs = sorted(chapters(), key=lambda c: c.get("told", ""))
    t = (title or "").lower().strip()
    pick = next((c for c in reversed(chs) if t and t in c["title"].lower()), None) if t else None
    pick = pick or (chs[-1] if chs else None)
    if not pick:
        return {"error": "no chapter yet", "say": "There's no story written down yet, sir. Say 'I want to tell you a "
                                                  "story' and I'll start one."}
    session = Path(pick["session"])
    try:
        old = session.read_text(encoding="utf-8")
    except OSError:
        old = ""
    now = datetime.now()
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(old.rstrip() + f"\n\n[{now:%H:%M}, added later] {text}\n", encoding="utf-8")
    raw = "\n\n".join(re.sub(r"^\[[^\]]*\]\s*", "", ln) for ln in session.read_text(encoding="utf-8").split("\n\n")
                      if ln.strip() and not ln.startswith("# "))
    try:
        data = _write_json(raw, f"the chapter \"{pick['title']}\", with what he added at the end (work it into the "
                                f"story where it belongs; keep the title unless it no longer fits)")
    except Exception as e:
        logger.warning(f"MEMOIR: couldn't rewrite the chapter ({e})")
        data = {}
    if not data.get("text"):
        return {"session": str(session), "error": "writer failed",
                "say": f"I kept your words with \"{pick['title']}\", sir, but couldn't rewrite the chapter just now."}
    try:
        when = datetime.fromisoformat(pick.get("told") or "")
    except ValueError:
        when = now
    entry = _render(data, raw, when, session, Path(pick["file"]))
    return {**entry, "added": len(text.split()), "say": f"Added to \"{entry['title']}\", sir."}


def compile_book() -> Optional[Path]:
    """Biography.md: every chapter in the order things happened (year, then when he told it)."""
    chs = sorted(chapters(), key=lambda c: (c.get("year") or 9999, c.get("told", "")))
    if not chs:
        return None
    out = ["# My life, as I told it to E.V.A.", "", "## Contents", ""]
    out += [f"{i}. {c['title']}" + (f" ({c['period']})" if c.get("period") else "") for i, c in enumerate(chs, 1)]
    for c in chs:
        try:
            text = Path(c["file"]).read_text(encoding="utf-8")
        except OSError:
            continue
        out += ["", "---", "", re.sub(r"^# ", "## ", text, count=1, flags=re.M)]
    p = folder() / "Biography.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


def open_questions(n: int = 5) -> list[str]:
    qs = []
    for c in reversed(chapters()):
        qs += c.get("questions") or []
    return qs[:n]


# ---------------------------------------------------------------- while it records (orchestrator)
def intercept(text: str) -> Optional[dict]:
    """Called with each sentence while it records. -> what to do, or None when it isn't recording.
    {"say": str | "", "silent": bool, "widget"?: {...}}"""
    st = status()
    if not st["active"]:
        if st["paused"] and RESUME.search(text or ""):
            start()
            return {"say": "Go on, sir, I'm listening.", "silent": False}
        return None
    t = (text or "").strip()
    if CANCEL.match(t):
        cancel()
        return {"say": "Alright, I've thrown that recording away.", "silent": False}
    if PAUSE.match(t):
        pause()
        return {"say": "Paused. Say 'carry on with the story' when you're ready.", "silent": False}
    if STOP.match(t):
        out = stop()
        w = {"kind": "note", "title": out.get("title") or "Your story", "text": _preview(out)} if out.get("file") \
            else None
        return {"say": out["say"], "silent": False, **({"widget": w} if w else {})}
    st = add(t)
    return {"say": "", "silent": True, "parts": st["parts"], "words": st["words"]}


def _preview(out: dict) -> str:
    try:
        text = Path(out["file"]).read_text(encoding="utf-8")
    except (OSError, KeyError):
        return ""
    return text[:3000] + ("\n\n..." if len(text) > 3000 else "") + f"\n\nSaved in {out['file']}"


# ---------------------------------------------------------------- her tools
def _r(data: dict, say: str = "", **extra) -> dict:
    out = {"result": json.dumps(data, ensure_ascii=False, default=str), **extra}
    if say:
        out["say"] = say
    return out


def tool_memoir_start(topic: str = "", **_):
    out = start(topic)
    say = "Go on, sir, I'm listening." if out.get("resumed") else \
        "I'm listening, sir. Take your time; say 'that's the end' when you're done."
    return _r({"recording": True, **out}, say, exact=True)


def tool_memoir_stop(**_):
    out = stop()
    w = {"kind": "note", "title": out.get("title") or "Your story", "text": _preview(out)} if out.get("file") else None
    return _r({k: v for k, v in out.items() if k != "say"}, out["say"], exact=True, **({"widget": w} if w else {}))


def tool_memoir_add(text: str = "", title: str = "", **_):
    out = add_to_chapter(text, title)
    return _r({k: v for k, v in out.items() if k != "say"}, out["say"], exact=True)


def tool_memoir_list(**_):
    chs = chapters()
    return _r({"folder": str(folder()), "chapters": [{k: c.get(k) for k in ("title", "period", "people", "places",
                                                                            "summary", "told")} for c in chs],
               "open_questions": open_questions(), "recording": status()})


def tool_memoir_read(title: str = "", **_):
    t = (title or "").lower()
    for c in reversed(chapters()):
        if t and (t in c["title"].lower() or any(t in p.lower() for p in c.get("people") or [])):
            try:
                return _r({"title": c["title"], "text": Path(c["file"]).read_text(encoding="utf-8")[:12000]})
            except OSError:
                break
    return _r({"error": f"no chapter about {title}", "titles": [c["title"] for c in chapters()]})


SCHEMAS = [
    {"type": "function", "function": {
        "name": "memoir_start", "description": "Start (or carry on) recording one of his life stories for his "
                                               "biography: 'I want to tell you a story', 'record my story', "
                                               "'interview me'. You then listen quietly until he says it's the end.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string",
                                                                  "description": "what it's about, if he said"}}}}},
    {"type": "function", "function": {
        "name": "memoir_stop", "description": "He finished his story: write it down as a chapter of his biography.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "memoir_add", "description": "Add what he just said to a chapter of his biography and rewrite it: "
                                             "his answer to the question you asked after a story, or 'oh, and also...' "
                                             "about a story he told. The latest chapter unless he names one. Pass his "
                                             "words as he said them.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "title": {"type": "string"}},
                       "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "memoir_list", "description": "His biography so far: chapters, periods, people, the questions you "
                                              "wanted to ask him next.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "memoir_read", "description": "Read one chapter of his biography (by title words or a person in it).",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
]
FUNCTIONS = {"memoir_start": tool_memoir_start, "memoir_stop": tool_memoir_stop, "memoir_list": tool_memoir_list,
             "memoir_read": tool_memoir_read, "memoir_add": tool_memoir_add}
ACTIONS = {"memoir_start", "memoir_stop", "memoir_add"}
