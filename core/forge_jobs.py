"""
FORGE background jobs (v0.2.5 milestone 6).

"Build a skill that tells me the moon phase" no longer blocks the conversation:

    forge_build (after you confirm)  ->  a job in the queue  ->  a worker thread runs Forge.build()
    ->  no time limit (a local 14B coder can take a while)  ->  when it's done:
          PULSE "forge.done"  ->  every open E.V.A. window says it, with a yes/no to install
          Telegram            ->  a message with [Install] [Discard] buttons

One build at a time; more wait in the queue. Jobs survive in data/forge_jobs.json: a build that was
running when E.V.A. stopped is marked "interrupted" at the next start (nothing half-installed, ever:
installing is always a separate yes).

Cancelling a queued job drops it. Cancelling a running job lets the model call finish (it can't be cut
mid-request) and then throws the result away.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

JOBS_PATH = Path("data/forge_jobs.json")
KEEP = 30                                      # finished jobs remembered


@dataclass
class Job:
    id: str
    request: str
    status: str = "queued"                     # queued | running | done | failed | cancelled | interrupted
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    result_name: str = ""
    result_status: str = ""                    # ready | rejected | infeasible
    say: str = ""
    error: str = ""
    cancel_requested: bool = False
    stage: str = ""                            # planning | coding and writing tests | fixing, round 2 of 4 | reviewing


def pretty(name: str) -> str:
    return name.replace("_", " ")


def result_message(p) -> dict:
    """The words, card and follow-up for a finished build (Proposal). Shared by every channel."""
    cost = (f"It cost {p.cost_eur * 100:.0f} cents." if p.cost_eur >= 0.005
            else f"Built locally with {p.provider}, at no cost." if p.provider and p.provider != "Claude" else "")
    if p.status == "infeasible":
        return {"status": p.status, "say": f"I can't build that safely as a skill, sir. {p.reason} {cost}".strip()}
    if p.status != "ready":
        failing = [x.split(":")[0].replace("test_", "").replace("_", " ") for x in (p.reason or "").split("; ")
                   if x.startswith("test_")][:2]
        why = f" The tests about {' and '.join(failing)} kept failing." if failing else ""
        offer = ""
        try:
            from core.brain import get_brain
            if p.provider and p.provider != "Claude" and get_brain().cloud_ready():
                offer = " Say \"use Claude for FORGE\" and ask me again if you'd like a stronger model to try."
        except Exception:
            pass
        return {"status": p.status, "say": f"My draft of {pretty(p.name)} didn't pass its own checks, sir, so I've kept "
                                           f"it out.{why} {cost}{offer}".strip(),
                "widget": {"kind": "note", "title": f"Draft rejected · {pretty(p.name)}", "text": p.reason[:240]}}
    net = (f" It needs internet access to {', '.join(p.network_hosts)}." if p.network_hosts else " It works fully offline.")
    dropped = sum(len(f["msg"].split(":", 1)[1].split(",")) for f in (getattr(p, "review", None) or [])
                  if str(f.get("msg", "")).startswith("judge dropped tests"))
    caution = (f" Heads-up: I dropped {dropped} of its tests that looked wrong, so its answers aren't fully verified. "
               f"Check a couple of answers you know before relying on it.") if dropped else ""
    say = (f"Your skill is ready, sir: {pretty(p.name)}. {p.summary}{net} It passed the security review and "
           f"{p.tests_passed} tests.{caution} {cost} Shall I install it?").replace("  ", " ")
    return {"status": "ready", "say": say, "name": p.name,
            "widget": {"kind": "forge", "name": pretty(p.name), "summary": p.summary, "tools": p.tools,
                       "hosts": p.network_hosts, "tests": p.tests_passed, "review_passed": True,
                       "cost": (f"EUR {p.cost_eur:.2f}" if p.cost_eur >= 0.005 else "Free, built locally")},
            "confirm_next": {"tool": "forge_install", "args": {"name": p.name},
                             "desc": f"install the {pretty(p.name)} skill"}}


class JobManager:
    def __init__(self, forge_factory: Optional[Callable] = None, on_done: Optional[Callable[[Job, dict], None]] = None,
                 threaded: bool = True):
        self._forge_factory = forge_factory
        self.on_done = on_done                 # set by the server; tests pass their own
        self.threaded = threaded
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.jobs: list[Job] = self._load()

    # ---------------------------------------------------------------- storage
    def _load(self) -> list[Job]:
        try:
            raw = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
            jobs = [Job(**{k: v for k, v in j.items() if k in Job.__dataclass_fields__}) for j in raw]
        except Exception:
            return []
        for j in jobs:
            if j.status == "running":          # E.V.A. stopped mid-build
                j.status, j.finished, j.error = "interrupted", time.time(), "E.V.A. was restarted during the build"
        return jobs

    def _save(self) -> None:
        with self._lock:
            done = [j for j in self.jobs if j.status not in ("queued", "running")][-KEEP:]
            live = [j for j in self.jobs if j.status in ("queued", "running")]
            self.jobs = sorted(done + live, key=lambda j: j.created)
            JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
            JOBS_PATH.write_text(json.dumps([asdict(j) for j in self.jobs], indent=1), encoding="utf-8")

    def _forge(self):
        if self._forge_factory:
            return self._forge_factory()
        from core.forge_engine import get_forge
        return get_forge()

    # ---------------------------------------------------------------- queue
    def submit(self, request: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], request=" ".join((request or "").split())[:500])
        with self._lock:
            self.jobs.append(job)
            self._save()
        logger.info(f"FORGE job {job.id} queued: {job.request}")
        if self.threaded:
            self._ensure_worker()
            self._wake.set()
        else:
            self._run(job)                     # tests: inline
        return job

    def queued_before(self, job: Job) -> int:
        with self._lock:
            return sum(1 for j in self.jobs if j.status in ("queued", "running") and j.created < job.created)

    def running(self) -> Optional[Job]:
        with self._lock:
            return next((j for j in self.jobs if j.status == "running"), None)

    def waiting(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.status == "queued"]

    def latest(self) -> Optional[Job]:
        with self._lock:
            return self.jobs[-1] if self.jobs else None

    def cancel(self, job_id: str = "") -> Optional[Job]:
        with self._lock:
            live = [j for j in self.jobs if j.status in ("queued", "running") and (not job_id or j.id == job_id)]
            if not live:
                return None
            job = live[-1]
            if job.status == "queued":
                job.status, job.finished = "cancelled", time.time()
            else:
                job.cancel_requested = True
            self._save()
            return job

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, daemon=True, name="forge-jobs")
        self._worker.start()

    def _loop(self) -> None:
        while True:
            nxt = None
            with self._lock:
                nxt = next((j for j in self.jobs if j.status == "queued"), None)
            if nxt is None:
                self._wake.wait(timeout=60)
                self._wake.clear()
                continue
            self._run(nxt)

    def _run(self, job: Job) -> None:
        with self._lock:
            job.status, job.started = "running", time.time()
            self._save()
        logger.info(f"FORGE job {job.id} building (no time limit)")
        msg: dict = {}
        try:
            def progress(stage: str) -> None:
                job.stage = stage
                logger.info(f"FORGE job {job.id}: {stage}")
            forge = self._forge()
            try:
                p = forge.build(job.request, progress=progress)
            except TypeError:                   # an older or fake forge without progress reports
                p = forge.build(job.request)
            msg = result_message(p)
            job.result_name, job.result_status, job.say = p.name, p.status, msg["say"]
            job.status = "done"
        except Exception as e:
            job.status, job.error = "failed", str(e)[:300]
            job.say = f"The skill build failed, sir: {str(e)[:160]}."
            msg = {"status": "failed", "say": job.say}
            logger.error(f"FORGE job {job.id} failed: {e}")
        job.finished = time.time()
        if job.cancel_requested:
            job.status = "cancelled"
            if job.result_status == "ready":
                try:
                    self._forge().discard(job.result_name)        # you cancelled: never offer it
                except Exception:
                    pass
            msg = {}
        with self._lock:
            self._save()
        mins = (job.finished - job.started) / 60
        logger.info(f"FORGE job {job.id} {job.status} after {mins:.1f} min")
        if msg and self.on_done:
            try:
                self.on_done(job, msg)
            except Exception as e:                                 # a failed notification never loses the job
                logger.warning(f"FORGE job {job.id}: notification failed ({e})")


def offer_install(orch, msg: dict) -> None:
    """A finished, safe build waits for your yes: the next "yes" in that conversation installs it."""
    if msg.get("confirm_next"):
        orch.pending = {**msg["confirm_next"], "ts": time.time()}


_jobs: Optional[JobManager] = None


def get_jobs() -> JobManager:
    global _jobs
    if _jobs is None:
        _jobs = JobManager(on_done=default_on_done)
    return _jobs


def default_on_done(job: Job, msg: dict) -> None:
    """Tell every channel: PULSE for the web windows, Telegram with buttons."""
    from core.events.bus import get_bus
    get_bus().publish("forge.done", {"job": asdict(job), "message": msg})
    try:
        from core import telegram_bridge
        buttons = None
        if msg.get("status") == "ready":
            buttons = [[{"text": "Install", "callback_data": f"forge:install:{msg['name']}"},
                        {"text": "Discard", "callback_data": f"forge:discard:{msg['name']}"}]]
        telegram_bridge.send_from_thread(msg["say"], buttons=buttons)
    except Exception as e:
        logger.warning(f"FORGE: Telegram push failed ({e})")
