"""Meetings (Nov 23): "Eva, record this meeting" ... "Eva, stop recording" -> a transcript, a summary, decisions
and to-dos, saved as a note.

Where the sound comes from:
- said to your phone (hands-free on): the phone records with the wake-word microphone and sends the audio to this
  PC every 15 s, so nothing is lost if the phone dies. "Eva" still works meanwhile (voice-only answers);
- said at the PC: this PC's microphone (pip install sounddevice);
- a file: Pixel Recorder or any audio app, Share > E.V.A. on the phone.
  A shared transcript (text) is summarized as it is.

Transcription runs here on your GPU with Whisper large-v3-turbo (free, private, English and Romanian), loaded
only while it works. The notes are written by Claude when the cloud is allowed (about EUR 0.002 for an hour),
otherwise by your local model. Audio and transcripts stay in data/meetings (and your Obsidian vault, if set).
In the Netherlands you may record a conversation you take part in; tell the others, and ask before sharing it.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

DIR = Path("data/meetings")
SR = 16000
MAX_S = 3 * 3600
LATE_S = 20.0
DEFAULTS = {"whisper_model": "large-v3-turbo", "device": "cuda", "compute_type": "int8_float16",
            "notes_folder": "Meetings", "keep_audio_days": 30, "speakers": "auto",
            "diarization_model": "pyannote/speaker-diarization-community-1", "todo_hour": 9, "todo_minutes": 30}
NOTES_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "key_points": {"type": "array", "items": {"type": "string"}},
    "decisions": {"type": "array", "items": {"type": "string"}},
    "action_items": {"type": "array", "items": {"type": "object", "properties": {
        "task": {"type": "string"}, "owner": {"type": "string"}, "due": {"type": "string"}}, "required": ["task"]}},
    "open_questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "summary", "key_points", "decisions", "action_items"]}
NOTES_PROMPT = """You turn a meeting transcript into notes for Ionut (the person who recorded it). The transcript
comes from speech recognition: names and words may be misheard. Lines may start with a speaker label ("Speaker 1:");
when someone's name becomes clear (they introduce themselves, or are addressed by name), use the name instead of
the label; otherwise keep "Speaker 1". Without labels, infer who said what only when it's clear. Be faithful:
nothing that wasn't said. Write in the transcript's language.
title: 3-7 words. summary: 3-5 sentences. key_points: the important things said. decisions: what was agreed.
action_items: concrete tasks, with owner and due date only if they were said. open_questions: what stayed open."""


def cfg() -> dict:
    from core.settings import get_settings
    return {**DEFAULTS, **(get_settings().get("meetings") or {})}


def folder(mid: str) -> Path:
    return Path(DIR) / mid


def _meta_path(mid: str) -> Path:
    return folder(mid) / "meta.json"


def read_meta(mid: str) -> dict:
    try:
        return json.loads(_meta_path(mid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(mid: str, **changes) -> dict:
    meta = {**read_meta(mid), **changes}
    folder(mid).mkdir(parents=True, exist_ok=True)
    _meta_path(mid).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return meta


def all_meetings() -> list[dict]:
    if not Path(DIR).exists():
        return []
    out = [read_meta(p.name) for p in Path(DIR).iterdir() if p.is_dir() and (p / "meta.json").exists()]
    return sorted((m for m in out if m), key=lambda m: m.get("started", 0))


class Recorder:
    """One recording at a time. Audio arrives as PCM16 16 kHz mono (phone chunks, the PC microphone)."""

    def __init__(self):
        self.active: Optional[dict] = None
        self.late: dict[str, float] = {}         # stopped recordings whose last phone chunk may still come
        self._lock = threading.Lock()
        self._mic = None

    def start(self, title: str = "", source: str = "pc") -> dict:
        with self._lock:
            if self.active:
                return self.active
            mid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            meta = write_meta(mid, id=mid, title=title.strip(), source=source, started=time.time(),
                              status="recording", seconds=0.0)
            (folder(mid) / "audio.pcm").write_bytes(b"")     # raw while recording, a WAV when it stops
            self.active = meta
        logger.info(f"MEETING: recording {mid} from the {source}")
        return meta

    def append(self, mid: str, pcm: bytes) -> bool:
        with self._lock:
            live = self.active and self.active["id"] == mid
            if not pcm or not (live or self.late.get(mid, 0) > time.time()):
                return False
            path = folder(mid) / "audio.pcm"
            size = path.stat().st_size if path.exists() else 0
            if size / 2 / SR > MAX_S:
                return False
            with path.open("ab") as f:
                f.write(pcm[: len(pcm) - len(pcm) % 2])
            if live:
                self.active["seconds"] = (size + len(pcm)) / 2 / SR
            return True

    def stop(self) -> Optional[dict]:
        with self._lock:
            meta, self.active = self.active, None
        if self._mic is not None:
            try:
                self._mic.stop()
                self._mic.close()
            except Exception:
                pass
            self._mic = None
        if not meta:
            return None
        self.late[meta["id"]] = time.time() + LATE_S      # the phone's last chunk is on its way
        return write_meta(meta["id"], ended=time.time(), status="queued", seconds=meta.get("seconds", 0.0))

    def start_pc_mic(self, mid: str) -> Optional[str]:
        """This PC's microphone into the active recording. None, or why it can't."""
        try:
            import sounddevice as sd
        except Exception as e:
            return f"recording on the PC needs sounddevice (pip install sounddevice): {str(e)[:60]}"
        buf, last = bytearray(), [time.time()]

        def cb(indata, frames, t, status):
            buf.extend(bytes(indata))
            if time.time() - last[0] >= 5:
                chunk = bytes(buf)
                buf.clear()
                last[0] = time.time()
                threading.Thread(target=self.append, args=(mid, chunk), daemon=True).start()
        try:
            self._mic = sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", callback=cb, blocksize=1600)
            self._mic.start()
        except Exception as e:
            self._mic = None
            return f"the PC microphone didn't open ({str(e)[:80]})"
        return None


_recorder: Optional[Recorder] = None


def get_recorder() -> Recorder:
    global _recorder
    if _recorder is None:
        _recorder = Recorder()
    return _recorder


# ------------------------------------------------------------ transcribe, then notes
def finalize(mid: str) -> None:
    """The raw recording as a WAV file (once nothing more can arrive)."""
    raw = folder(mid) / "audio.pcm"
    if not raw.exists():
        return
    with wave.open(str(folder(mid) / "audio.wav"), "wb") as w, raw.open("rb") as f:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            w.writeframes(block)
    raw.unlink()


def transcribe(path: Path, model_factory: Optional[Callable] = None) -> list[dict]:
    """[{start, end, text}] with Whisper large-v3-turbo on the GPU (CPU if it must). Loaded only for this job."""
    c = cfg()
    if model_factory is None:
        from faster_whisper import WhisperModel

        def model_factory():
            try:
                return WhisperModel(c["whisper_model"], device=c["device"], compute_type=c["compute_type"])
            except Exception as e:
                logger.warning(f"MEETING: GPU Whisper failed ({e}); using the CPU, slower")
                return WhisperModel(c["whisper_model"], device="cpu", compute_type="int8")
    model = model_factory()
    try:
        segments, info = model.transcribe(str(path), vad_filter=True, beam_size=5, condition_on_previous_text=False)
        out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()]
        logger.info(f"MEETING: transcribed {len(out)} segments ({getattr(info, 'language', '?')})")
        return out
    finally:
        del model
        import gc
        gc.collect()


def _hf_token() -> Optional[str]:
    import os
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    try:
        from core.security.vault import Vault
        v = Vault()
        v.load()
        return v.get_optional("HF_TOKEN")
    except Exception:
        return None


def diarize(path) -> Optional[list[tuple[float, float, str]]]:
    """Who spoke when, with pyannote.audio 4 (speaker-diarization-community-1) on the GPU, or None when it isn't
    set up: pip install pyannote.audio, accept the model's conditions on Hugging Face, HF_TOKEN in
    config/secrets.env. meetings.speakers: auto (when set up) | off."""
    c = cfg()
    if str(c.get("speakers", "auto")).lower() in ("off", "false", "no"):
        return None
    token = _hf_token()
    if not token:
        return None
    try:
        from pyannote.audio import Pipeline
    except Exception:
        logger.info("MEETING: speaker labels need pyannote.audio (pip install pyannote.audio)")
        return None
    try:
        pipe = Pipeline.from_pretrained(c["diarization_model"], token=token)
        try:
            import torch
            if c.get("device", "cuda") == "cuda" and torch.cuda.is_available():
                pipe.to(torch.device("cuda"))
        except Exception:
            pass
        out = pipe(str(path))
        turns = [(float(t.start), float(t.end), str(spk)) for t, spk in out.speaker_diarization]
        logger.info(f"MEETING: {len({s for _, _, s in turns})} speakers in {len(turns)} turns")
        return turns
    except Exception as e:
        logger.warning(f"MEETING: speaker labels failed ({str(e)[:120]}); notes without them")
        return None


def label_speakers(segments: list[dict], turns: Optional[list[tuple[float, float, str]]]) -> list[dict]:
    """Each segment gets the speaker who overlaps it most, named Speaker 1, 2... in order of first appearance."""
    if not turns:
        return segments
    names: dict[str, str] = {}
    for s in segments:
        best, most = None, 0.0
        for a, b, spk in turns:
            o = min(b, s["end"]) - max(a, s["start"])
            if o > most:
                best, most = spk, o
        if best is not None:
            names.setdefault(best, f"Speaker {len(names) + 1}")
            s["speaker"] = names[best]
    return segments


def transcript_text(segments: list[dict]) -> str:
    return "\n".join(f"[{int(s['start'] // 60):02d}:{int(s['start'] % 60):02d}] "
                     + (f"{s['speaker']}: " if s.get("speaker") else "") + s["text"] for s in segments)


def make_notes(transcript: str, title_hint: str = "") -> dict:
    """Claude when the cloud is allowed (better notes), otherwise the local model."""
    user = (f"Title given: {title_hint}\n\n" if title_hint else "") + f"Transcript:\n{transcript[:180000]}"
    try:
        from core.understand import brains
        if brains.cloud_allowed():
            from core.claude import ClaudeClient
            out = ClaudeClient(model=brains.cfg()["claude_model"], timeout=180).message(
                system=NOTES_PROMPT, messages=[{"role": "user", "content": user}], max_tokens=4000,
                tools=[{"name": "notes", "description": "The meeting notes.", "input_schema": NOTES_SCHEMA}],
                tool_choice={"type": "tool", "name": "notes"}, purpose="meeting-notes")
            if out.get("tool_input"):
                return out["tool_input"]
    except Exception as e:
        logger.warning(f"MEETING: Claude didn't write the notes ({str(e)[:100]}); using the local model")
    import httpx
    from core.settings import get_settings
    local = get_settings()["inference"]["local"]
    r = httpx.post(f"{local['base_url']}/api/chat", timeout=600, json={
        "model": local.get("answer_model") or local.get("decision_model"), "stream": False, "format": NOTES_SCHEMA,
        "options": {"temperature": 0.2, "num_ctx": 32768},
        "messages": [{"role": "system", "content": NOTES_PROMPT}, {"role": "user", "content": user[:60000]}]})
    return json.loads(r.json()["message"]["content"])


def notes_markdown(meta: dict, notes: dict, transcript: str) -> str:
    when = time.strftime("%A %d %B %Y, %H:%M", time.localtime(meta.get("started", time.time())))
    mins = round(meta.get("seconds", 0) / 60)
    lines = [f"# {notes.get('title') or meta.get('title') or 'Meeting'}", "",
             f"*{when}" + (f", {mins} min" if mins else "") + "*", "", "## Summary", notes.get("summary", ""), ""]

    def section(name, items, fmt=str):
        if items:
            lines.extend([f"## {name}", *[f"- {fmt(i)}" for i in items], ""])
    section("Key points", notes.get("key_points"))
    section("Decisions", notes.get("decisions"))
    section("To-dos", notes.get("action_items"), lambda a: "[ ] " + a.get("task", "") +
            (f" ({a['owner']})" if a.get("owner") else "") + (f", due {a['due']}" if a.get("due") else ""))
    section("Open questions", notes.get("open_questions"))
    lines.extend(["## Transcript", transcript, ""])
    return "\n".join(lines)


def _vault_copy(meta: dict, md: str) -> Optional[Path]:
    try:
        from core.settings import get_settings
        vault = (get_settings().get("obsidian") or {}).get("vault_path")
        if not vault:
            return None
        d = Path(vault) / cfg()["notes_folder"]
        d.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w\s-]", "", md.splitlines()[0].lstrip("# "))[:60].strip() or "Meeting"
        p = d / f"{time.strftime('%Y-%m-%d', time.localtime(meta.get('started', time.time())))} {name}.md"
        p.write_text(md, encoding="utf-8")
        return p
    except Exception as e:
        logger.warning(f"MEETING: couldn't write to the vault ({e})")
        return None


def process(mid: str, model_factory: Optional[Callable] = None, notes_fn: Optional[Callable] = None,
            diarizer: Optional[Callable] = None) -> dict:
    """Transcribe (unless a transcript was given), label speakers when possible, write the notes. The finished meta."""
    meta = read_meta(mid)
    try:
        tpath = folder(mid) / "transcript.txt"
        finalize(mid)
        if not tpath.exists():
            write_meta(mid, status="transcribing")
            audio = next((p for p in folder(mid).iterdir() if p.name.startswith("audio") and p.suffix != ".pcm"), None)
            if audio is None:
                raise FileNotFoundError("no audio")
            segments = transcribe(audio, model_factory)
            if segments:
                segments = label_speakers(segments, (diarizer or diarize)(audio))
            tpath.write_text(transcript_text(segments), encoding="utf-8")
        transcript = tpath.read_text(encoding="utf-8")
        if not transcript.strip():
            return write_meta(mid, status="empty")
        write_meta(mid, status="summarizing")
        notes = (notes_fn or make_notes)(transcript, meta.get("title", ""))
        md = notes_markdown(read_meta(mid), notes, transcript)
        (folder(mid) / "notes.md").write_text(md, encoding="utf-8")
        (folder(mid) / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
        vault = _vault_copy(meta, md)
        return write_meta(mid, status="done", title=notes.get("title") or meta.get("title", ""),
                          todos=len(notes.get("action_items") or []), vault=str(vault or ""))
    except Exception as e:
        logger.exception(f"MEETING {mid}: failed ({e})")
        return write_meta(mid, status="failed", error=str(e)[:200])


def process_in_background(mid: str, tell: Callable[[str], None], wait: float = 0.0) -> None:
    def run():
        if wait:
            time.sleep(wait)                              # the phone's last chunk
        meta = process(mid)
        if meta.get("status") == "done":
            n = meta.get("todos", 0)
            offer = (" Say 'put the to-dos in my reminders' or 'in my calendar' and I'll add yours." if n else "")
            tell(f"Your notes from {meta.get('title') or 'the meeting'} are ready, sir: "
                 f"{n} to-do{'s' if n != 1 else ''}. Ask me 'what were the to-dos?' or find them in your notes.{offer}")
        elif meta.get("status") == "empty":
            tell("I couldn't hear any speech in that recording, sir.")
        else:
            tell(f"I couldn't make notes from that recording, sir: {meta.get('error', 'unknown error')}.")
    threading.Thread(target=run, daemon=True, name=f"meeting-{mid}").start()


def add_file(name: str, data: bytes, title: str = "") -> dict:
    """A shared audio file or a transcript (text)."""
    mid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    folder(mid).mkdir(parents=True, exist_ok=True)
    ext = (Path(name).suffix or ".bin").lower()[:6]
    if ext in (".txt", ".md") or name == "text":
        (folder(mid) / "transcript.txt").write_text(data.decode("utf-8", errors="replace"), encoding="utf-8")
    else:
        (folder(mid) / f"audio{ext}").write_bytes(data)
    return write_meta(mid, id=mid, title=title or Path(name).stem, source="file", started=time.time(),
                      ended=time.time(), status="queued", seconds=0.0)


# ------------------------------------------------------------ tools
def tool_meeting_record(on: bool = True, title: str = "", **_):
    from core import device
    rec = get_recorder()
    if on:
        if rec.active:
            return {"result": json.dumps({"recording": rec.active["id"]}), "exact": True,
                    "say": "I'm already recording, sir. Say 'stop recording' when you're done."}
        here = device.CURRENT.get()
        on_phone = here is not None and "record" in (getattr(here, "device_caps", None) or ())
        meta = rec.start(title, "phone" if on_phone else "pc")
        if on_phone:
            out = device._ask("record", "record_start", {"id": meta["id"]})
            if not out.get("ok"):
                rec.stop()
                return {"result": json.dumps({"error": out.get("error")}), "exact": True,
                        "say": f"I couldn't record on your phone, sir: {out.get('error') or 'no reason given'}. "
                               f"Recording needs Hands-free on."}
        else:
            why = rec.start_pc_mic(meta["id"])
            if why:
                rec.stop()
                return {"result": json.dumps({"error": why}), "say": f"I can't record here, sir: {why}.", "exact": True}
        return {"result": json.dumps({"recording": meta["id"]}), "exact": True,
                "say": "Recording, sir. Let the others know. Say 'Eva, stop recording' when it's over, and I'll "
                       "write the notes."}
    if not rec.active:
        return {"result": json.dumps({"error": "not recording"}), "say": "I'm not recording anything, sir.",
                "exact": True}
    phone = rec.active.get("source") == "phone"
    if phone:
        device._ask("record", "record_stop", {"id": rec.active["id"]})   # it sends its last seconds
    meta = rec.stop()
    _announce_when_done(meta["id"], wait=LATE_S if phone else 0.0)
    mins = max(1, round(meta.get("seconds", 0) / 60))
    return {"result": json.dumps({"stopped": meta["id"]}), "exact": True,
            "say": f"Stopped, sir: {mins} minute{'s' if mins != 1 else ''} recorded. I'll tell you when the notes "
                   f"are ready."}


def _announce_when_done(mid: str, wait: float = 0.0) -> None:
    from core.events.bus import get_bus
    process_in_background(mid, lambda text: get_bus().publish("forge.done", {"message": {"say": text}}), wait)


def tool_meeting_notes(what: str = "summary", **_):
    done = [m for m in all_meetings() if m.get("status") == "done"]
    if not done:
        busy = [m for m in all_meetings() if m.get("status") in ("queued", "transcribing", "summarizing")]
        return {"result": json.dumps({"error": "none"}), "exact": True,
                "say": "The notes are still being written, sir." if busy else "I don't have any meeting notes yet, sir."}
    m = done[-1]
    notes = json.loads((folder(m["id"]) / "notes.json").read_text(encoding="utf-8"))
    w = (what or "summary").lower()
    if w.startswith(("todo", "to-do", "to do", "action", "task")):
        items = notes.get("action_items") or []
        say = ("No to-dos came out of it, sir." if not items else
               f"From {notes.get('title')}, sir: " + "; ".join(
                   a["task"] + (f" ({a['owner']})" if a.get("owner") else "") for a in items[:8]) + ".")
    elif w.startswith("decision"):
        items = notes.get("decisions") or []
        say = "Nothing was decided, sir." if not items else "Decided, sir: " + "; ".join(items[:6]) + "."
    else:
        say = f"{notes.get('title')}, sir: {notes.get('summary', '')}"
    return {"result": json.dumps({"meeting": m["id"]}), "say": say, "exact": True,
            "widget": {"kind": "note", "title": notes.get("title", "Meeting"), "text": notes.get("summary", "")[:400]}}


# ------------------------------------------------------------ to-dos into reminders or the calendar (Nov 23)
_ME = {"", "ionut", "ionuț", "me", "i", "myself", "you", "sir"}


def _latest_done() -> Optional[tuple[dict, dict]]:
    done = [m for m in all_meetings() if m.get("status") == "done"]
    if not done:
        return None
    m = done[-1]
    try:
        return m, json.loads((folder(m["id"]) / "notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mine(item: dict) -> bool:
    owner = (item.get("owner") or "").strip().lower()
    try:
        from core.messages import cfg as mcfg
        names = {n.lower() for n in (mcfg().get("my_names") or [])}
    except Exception:
        names = set()
    return owner in _ME or owner in names or any(owner.startswith(n + " ") for n in names | {"ionut"})


def todo_time(item: dict, now: Optional[datetime] = None) -> tuple[datetime, bool]:
    """(when, whether a date was said): its due day at todo_hour, or tomorrow morning."""
    now = now or datetime.now()
    c = cfg()
    from core.timeparse import parse_when
    due = parse_when(item.get("due") or "", now) if item.get("due") else None
    if due and due > now:
        if due.hour == 9 and due.minute == 0 and not re.search(r"\d|noon|morning|evening|afternoon",
                                                              item.get("due") or "", re.I):
            due = due.replace(hour=int(c["todo_hour"]), minute=0)
        return due, True
    t = (now + timedelta(days=1)).replace(hour=int(c["todo_hour"]), minute=0, second=0, microsecond=0)
    return t, False


def when_label(dt: datetime, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    days = (dt.date() - now.date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else dt.strftime("%A") if 1 < days < 7 else \
        dt.strftime("%a %d %b")
    return f"{day} {dt:%H:%M}"


def _pick(which: str = "mine") -> Optional[tuple[dict, dict, list[dict], list[dict]]]:
    got = _latest_done()
    if not got:
        return None
    meta, notes = got
    items = [i for i in (notes.get("action_items") or []) if (i.get("task") or "").strip()]
    if str(which or "mine").lower() == "all":
        return meta, notes, items, []
    return meta, notes, [i for i in items if _mine(i)], [i for i in items if not _mine(i)]


def describe_todos(args: dict) -> str:
    got = _pick(args.get("which") or "mine")
    where = "calendar" if args.get("where") == "calendar" else "reminders"
    if not got or not got[2]:
        return f"add the meeting to-dos to your {where}"
    meta, notes, mine, _ = got
    listed = "; ".join(f"{i['task']} ({when_label(todo_time(i)[0])})" for i in mine[:6])
    n = len(mine)
    return f"put {n} to-do{'s' if n != 1 else ''} from {notes.get('title') or 'the meeting'} in your {where}: {listed}"


def _plan_todos(where: str, which: str) -> tuple[Optional[str], dict]:
    """(why it can't go ahead, or None; what it would add). No side effects: also used before asking for a yes."""
    got = _pick(which)
    if not got:
        return "I don't have any meeting notes yet, sir.", {}
    meta, notes, mine, others = got
    title = notes.get("title") or "the meeting"
    if not mine:
        whose = ", ".join(sorted({(i.get("owner") or "someone").strip() for i in others}))
        return (f"No to-dos came out of {title}, sir." if not others else
                f"None of the to-dos from {title} are yours, sir. Say 'add all the to-dos' to add {whose}'s too."), {}
    done_before = set((meta.get("todos_added") or {}).get(where) or [])
    fresh = [i for i in mine if i["task"] not in done_before]
    if not fresh:
        return f"Those to-dos are already in your {where}, sir.", {}
    if where == "calendar":
        from core.google_api import connected
        if not connected():
            return "Your Google Calendar isn't connected, sir. I can put them in your reminders instead.", {}
    return None, {"meta": meta, "title": title, "fresh": fresh, "others": others, "done_before": done_before}


def _where(where: str) -> str:
    return "calendar" if (where or "").lower().startswith(("cal", "agenda", "sched")) else "reminders"


def precheck_todos(args: dict) -> Optional[str]:
    return _plan_todos(_where(args.get("where", "")), args.get("which") or "mine")[0]


def tool_meeting_todos_add(where: str = "reminders", which: str = "mine", **_):
    where = _where(where)
    why, plan = _plan_todos(where, which)
    if why:
        return {"result": json.dumps({"added": 0, "why": why}), "exact": True, "say": why}
    meta, title, fresh, others, done_before = (plan["meta"], plan["title"], plan["fresh"], plan["others"],
                                              plan["done_before"])
    google = None
    if where == "calendar":
        from core.google_api import get_google
        google = get_google()
    added = []
    for i in fresh:
        when, said = todo_time(i)
        if google is not None:
            google.insert_event(f"To-do: {i['task']}", when, when + timedelta(minutes=int(cfg()["todo_minutes"])),
                                f"From the meeting notes: {title}")
        else:
            from core.oracle import get_oracle
            get_oracle().store.add(i["task"], when)
        day = when_label(when).split(" ")[0]
        added.append(f"{i['task']} {'on ' if day not in ('today', 'tomorrow') else ''}{day} at {when:%H:%M}")
    write_meta(meta["id"], todos_added={**(meta.get("todos_added") or {}), where: sorted(done_before | {
        i["task"] for i in fresh})})
    n = len(added)
    say = f"Added {n} to-do{'s' if n != 1 else ''} from {title} to your {where}, sir: " + "; ".join(added) + "."
    if others:
        names = sorted({(i.get("owner") or "someone").strip() for i in others})
        say += (f" I left out 1 that's {names[0]}'s." if len(others) == 1 else
                f" I left out {len(others)} that belong to others ({', '.join(names)}).")
    return {"result": json.dumps({"added": n, "where": where}), "say": say, "exact": True}


SCHEMAS = [
    {"type": "function", "function": {"name": "meeting_record",
     "description": "Start (on) or stop (off) recording a meeting or conversation to get a transcript, summary and "
                    "to-dos ('record this meeting', 'stop recording').",
     "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}, "title": {"type": "string"}},
                    "required": ["on"]}}},
    {"type": "function", "function": {"name": "meeting_notes",
     "description": "What came out of the last recorded meeting: its summary, to-dos or decisions.",
     "parameters": {"type": "object", "properties": {
         "what": {"type": "string", "enum": ["summary", "todos", "decisions"]}}}}},
]
SCHEMAS.append({"type": "function", "function": {"name": "meeting_todos_add",
    "description": "Put the user's to-dos from the last recorded meeting into their reminders or Google Calendar "
                   "('put the to-dos in my reminders', 'add the action items to my calendar'). Asks first.",
    "parameters": {"type": "object", "properties": {
        "where": {"type": "string", "enum": ["reminders", "calendar"]},
        "which": {"type": "string", "enum": ["mine", "all"], "description": "'all' only when the user says all."}}}}})
FUNCTIONS = {"meeting_record": tool_meeting_record, "meeting_notes": tool_meeting_notes,
             "meeting_todos_add": tool_meeting_todos_add}
GUARDS = {"meeting_record": r"\b(record|recording|transcribe|transcription)\b",
          "meeting_notes": r"\b(meeting|to-?dos?|decid|notes|summary|action items?)\b",
          "meeting_todos_add": r"\b(to-?dos?|todos|action items?|tasks)\b"}
ACTIONS = {"meeting_record", "meeting_todos_add"}
CONFIRM = {"meeting_todos_add": describe_todos}
PRECHECK = {"meeting_todos_add": precheck_todos}         # nothing to add: say why instead of asking for a yes
EXACT = set(FUNCTIONS)
