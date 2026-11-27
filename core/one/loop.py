"""The one brain's turn (v1.0): think, use tools, let the risk judge decide, keep the journal, speak as it comes.

    async for event in OneBrain(orch).turn("move everything between 9 and 9:45 fifteen minutes later"):
        ...   # {"type": "thinking"|"token"|"step"|"widget"|"final"|"journal", ...}

Per step the backend gets the transcript and the tools; its words are spoken sentence by sentence (a sentence that
claims an action nobody ran, or a live fact nobody checked, is held back). Each tool call goes through the risk
judge: free and tell run, ask is not run (she asks in her own words and his yes runs it next turn). A finished
answer is checked once more (made-up numbers, promises without a tool call); a failed check is sent back to her to
fix, and with backend local_first a local answer that still fails, a slow or broken local model, or hand_to_cloud
moves the turn to Claude, with what already ran carried over (nothing runs twice).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator, Optional

from loguru import logger

from core.one import HIDDEN, backend_choice, cfg, journal, made, prompt, risk
from core.one.backends import BackendError, CloudBackend, LocalBackend, Step
from core.one.tools import HAND_TO_CLOUD, LOOP_NAMES, LOOP_TOOLS, THINK_HARDER

CORE_LOCAL = ["web_search", "web_read", "get_datetime", "task_start", "task_status", "task_answer", "task_cancel",
              "remember_fact", "recall_memory", "what_i_did", "change_setting", "calculate"]
_PROMISE = re.compile(r"\b(?:i'?ll|i will|i'?m going to|let me|i am (?:now )?|i'?m (?:now )?)\s*(?:go ahead and |now )?"
                      r"(?:perform|search|check|look|start|run|open|move|add|send|set|turn|play|proceed|create|build|"
                      r"find|fetch|get)\w*\b|\b(?:performing|searching|checking|looking up|proceeding)\b", re.I)


_REPORTS = {"what_i_did", "task_status", "task_list", "task_show", "recall_memory", "find_in_chats", "later_list",
            "list_reminders", "forge_status", "project_show"}
_EARLIER = re.compile(r"\b(?:earlier|already|before|yesterday|this morning|last time|a moment ago|just now)\b", re.I)


def _promises(text: str) -> bool:
    """'Performing the web search for NVIDIA's stock price today, sir.' with no search (Nov 27 19:55)."""
    t = (text or "").strip()
    if not t or t.endswith("?"):
        return False
    return any(_PROMISE.search(s) and not s.strip().endswith("?") for s in re.split(r"(?<=[.!])\s+", t))


class _OneSpeech:
    """Sentences as they come (the classic guards), and one more: 'Let me check your calendar' waits until the step
    ends. With a tool call after it, it's true and is spoken; without one it was an empty promise and the answer
    goes back to her."""

    def __init__(self, ran: list, words: bool = False):
        from core.orchestrator_hybrid import _Speech
        self.inner = _Speech(ran, words)
        self.words = words
        self.promised: list[str] = []

    @property
    def held(self) -> list[str]:
        return self.inner.held

    def _pass(self, sentences: list[str]) -> list[str]:
        out = []
        for sent in sentences:
            if self.promised or (not self.words and _promises(sent)):
                self.promised.append(sent)
                continue
            out.extend(self.inner._pass([sent]))
        return out

    def feed(self, text: str) -> list[str]:
        return self._pass(self.inner.chunker.feed(text))

    def flush(self, called: bool) -> list[str]:
        rest = self.inner.chunker.flush()
        out = self._pass([rest]) if rest else []
        if called and self.promised:              # the promise came true: say it (and what waited behind it)
            kept, self.promised = self.promised, []
            out = self.inner._pass(kept) + out
        return out


class OneBrain:
    def __init__(self, orch):
        self.orch = orch
        self.backend_override = None          # tests and the JARVIS run put a ScriptedBackend here
        self._local: Optional[LocalBackend] = None
        self._cloud: Optional[CloudBackend] = None
        self._tool_vecs: dict[str, object] = {}
        self.approved: set[str] = set()       # tools his yes covers this turn
        self.last_turn: Optional[dict] = None

    # ------------------------------------------------------------ backends
    def local(self) -> LocalBackend:
        c = cfg()
        from core.settings import local_cfg
        loc = local_cfg()
        want = c.get("local_model") or loc["decision_model"]
        if self._local is None or self._local.want != want:
            self._local = LocalBackend(loc["base_url"], want, c.get("local_fallback_model") or loc["decision_model"],
                                       think=bool(c.get("local_think", True)), num_ctx=int(c.get("local_ctx", 12288)),
                                       keep_alive=loc.get("keep_alive", "30m"),
                                       timeout_s=float(c.get("local_timeout_s", 150)))
        return self._local

    def cloud(self) -> CloudBackend:
        c = cfg()
        if self._cloud is None or self._cloud.model != c["cloud_model"]:
            self._cloud = CloudBackend(c["cloud_model"], c.get("cloud_effort") or None)
        return self._cloud

    def plan(self) -> list:
        """The backends this turn may use, in order."""
        if self.backend_override is not None:
            b = self.backend_override
            return list(b) if isinstance(b, (list, tuple)) else [b]
        choice = backend_choice()
        cloud_ok = self.cloud().available()
        if choice == "cloud":
            return [self.cloud()] if cloud_ok else [self.local()]
        if choice == "local" or not cloud_ok:
            return [self.local()]
        return [self.local(), self.cloud()]

    # ------------------------------------------------------------ tools
    def _all_schemas(self) -> list[dict]:
        belt = self.orch.belt
        out = [t for t in belt.schemas if t["function"]["name"] not in HIDDEN]
        names = {t["function"]["name"] for t in out}
        out += [t for t in made.schemas() if t["function"]["name"] not in names]
        return out

    def _vecs(self, schemas: list[dict]) -> dict:
        missing = [t for t in schemas if t["function"]["name"] not in self._tool_vecs]
        cortex = getattr(self.orch, "cortex", None)
        if missing and cortex is not None and getattr(cortex, "embed", None):
            try:
                import numpy as np
                texts = [f"search_document: {t['function']['name'].replace('_', ' ')}: "
                         f"{t['function'].get('description', '')[:300]}" for t in missing]
                vecs = cortex.embed(texts) or []
                for t, v in zip(missing, vecs):
                    a = np.asarray(v, dtype=np.float32)
                    n = float(np.linalg.norm(a))
                    self._tool_vecs[t["function"]["name"]] = a / n if n else None
            except Exception as e:
                logger.debug(f"BRAIN: no tool embeddings ({e})")
        return self._tool_vecs

    def rank(self, need: str, schemas: list[dict], n: int, keep: Optional[list[str]] = None,
             qvec=None) -> list[dict]:
        from core.understand.retrieve import tool_words, words
        q = words(need)
        vecs = self._vecs(schemas) if qvec is not None else {}
        scored = []
        for i, t in enumerate(schemas):
            name = t["function"]["name"]
            s = 0.25 * len(q & tool_words(t))
            v = vecs.get(name)
            if v is not None and qvec is not None:
                try:
                    s += float((v * qvec).sum()) * 3
                except Exception:
                    pass
            if keep and name in keep:
                s += 100
            scored.append((s, -i, name))
        best = {name for _, _, name in sorted(scored, reverse=True)[:n]}
        return [t for t in schemas if t["function"]["name"] in best]

    def tools_for(self, kind: str, user_input: str, qvec=None, extra: Optional[set] = None,
                  can_hand_over: bool = False) -> list[dict]:
        schemas = self._all_schemas()
        if kind == "cloud":
            out = schemas + LOOP_TOOLS + [THINK_HARDER]
            try:
                from core import browser as brw
                if brw.allowed():
                    out = out + [brw.ENTRY]
            except Exception:
                pass
            return out
        recent = sorted(self.orch._recent()) if hasattr(self.orch, "_recent") else []
        keep = CORE_LOCAL + recent + sorted(extra or ())
        picked = self.rank(user_input, schemas, int(cfg().get("local_tools", 22)) + len(keep), keep, qvec)
        slim = [{"type": "function", "function": {**t["function"],
                                                  "description": (t["function"].get("description") or "")[:300]}}
                for t in picked]
        return slim + LOOP_TOOLS + ([HAND_TO_CLOUD] if can_hand_over else [])

    # ------------------------------------------------------------ checks
    def check(self, text: str, gathered: list, user_input: str, words: bool, spoke_step_calls: bool) -> str:
        from core.orchestrator_hybrid import claims, ungrounded_numbers, unchecked_fact
        if words or not text:
            return ""
        ran = [t for t, _ in gathered]
        acted = any(self.orch.belt.is_action(t) and '"error"' not in (o.get("result") or "") for t, o in gathered)
        reporting = bool(set(ran) & _REPORTS) or bool(_EARLIER.search(text))    # what she did before is a report
        if claims(text) and not acted and not reporting:
            return "you said you did something, but no action ran this turn."
        if _promises(text) and not spoke_step_calls:
            return "you said you'd do something, but you didn't call a tool for it."
        kind = unchecked_fact(text, ran)
        if kind:
            return f"you stated something about his {kind} without checking it this turn."
        data = " ".join((o.get("result") or "") + " " + str(o.get("say") or "") for _, o in gathered)
        if gathered and any(t in ("web_search", "web_read", "crypto_price", "nearby") for t in ran):
            bad = ungrounded_numbers(text, data)
            if bad:
                return f"these numbers aren't in your tool results: {', '.join(bad[:4])}."
        return ""

    # ------------------------------------------------------------ the turn
    async def turn(self, user_input: str, note: str = "", images: Optional[list] = None,
                   via: str = "voice", ran: Optional[list] = None) -> AsyncIterator[dict]:
        """ran: [(tool, args, out, why)] already done this turn (his yes ran them): journaled and in the context."""
        from core import mind
        orch = self.orch
        c = cfg()
        rec = journal.Turn(user_input, chat=getattr(orch, "chat_id", "") or "", via=via)
        ctx: dict = {}
        try:
            orch._context(user_input, ctx)
        except Exception:
            ctx = {"facts": []}
        qvec = None
        try:
            qvec = orch.cortex.embed_query(user_input)
        except Exception:
            pass
        situation = prompt.situation(orch, note)
        facts = mind.facts_text(ctx.get("facts"))
        head = f"<situation>\n{situation}\n</situation>\n"
        if facts:
            head += f"<what he told you before>\n{facts}\n</what he told you before>\n"
        prior = mind.build_messages(orch.history, user_input, "", "", int(c["history_turns"]))[:-1]
        transcript = [{"role": m["role"], "text": m["content"]} for m in prior]
        transcript.append({"role": "user", "text": head + user_input, "images": list(images or [])})
        gathered: list = []
        used: set[str] = set()
        for tool, a, out, why in ran or []:
            rec.step(tool, a, "ask", why, "approved", out)
            gathered.append((tool, out))
            used.add(tool)
        cost = 0.0
        plan = self.plan()
        answer, carried = "", ""
        base = [dict(m) for m in transcript]
        for i, backend in enumerate(plan):
            more = i + 1 < len(plan)
            if carried:                                # a handover: Claude starts fresh, with what already ran
                transcript = [dict(m) for m in base]
                transcript[-1] = {**transcript[-1], "text": transcript[-1]["text"] + "\n\n" + carried}
            self._outcome = None
            async for ev in self._attempt(backend, transcript, gathered, used, rec, user_input, qvec, more):
                yield ev
            kind, value, spent = self._outcome or ("answer", "", 0.0)
            cost += spent
            if kind == "handover" and more:
                rec.data["escalated"] = f"{getattr(backend, 'model', backend.kind)} -> cloud: {value}"
                logger.info(f"BRAIN: the local model handed over ({value}); Claude takes this turn")
                done = [f"{t} -> {(o.get('say') or o.get('result') or '')[:300]}" for t, o in gathered]
                carried = ("(Already done this turn, don't repeat it: " + "; ".join(done) + ")") if done else \
                    "(Nothing has run yet this turn.)"
                yield {"type": "thinking", "text": f"\n[handing this to Claude: {value}]\n"}
                continue
            answer = value if kind == "answer" else ""
            break
        if not answer:
            says = [o.get("say") for _, o in gathered if o.get("say")]
            answer = " ".join(dict.fromkeys(says)) or (
                "My local brain couldn't work that out, sir, and Claude isn't available right now."
                if self._outcome and self._outcome[0] == "handover" else "I couldn't work that out, sir. Try me again?")
        orch._finish(user_input, answer, used)
        self.approved = set()
        data = rec.finish(answer, cost)
        self.last_turn = data
        yield {"type": "final", "text": answer, **({"replaced": True} if getattr(self, "_replaced", False) else {})}
        yield {"type": "journal", "turn": data["id"], "steps": len(data["steps"]), "brain": data["brain"],
               "escalated": data["escalated"], "cost_eur": data["cost_eur"]}

    async def _attempt(self, backend, transcript: list, gathered: list, used: set, rec, user_input: str, qvec,
                       can_hand_over: bool) -> AsyncIterator[dict]:
        """One backend's go at the turn. Sets self._outcome = (answer|handover, text|reason, cost)."""
        from core import mind
        from core.orchestrator_hybrid import creative
        c = cfg()
        orch = self.orch
        kind = getattr(backend, "kind", "local")
        system = prompt.system(local=kind != "cloud")
        extra: set[str] = set()
        tools = self.tools_for("cloud" if kind == "cloud" else "local", user_input, qvec, extra, can_hand_over)
        words = creative(user_input)
        loop = asyncio.get_running_loop()
        corrections, cost, model, effort = 0, 0.0, None, None
        self._replaced = False
        spoke = False
        asked = False
        max_tokens = int(c["max_tokens"])
        mins = re.search(r"\b(\d{1,2})\s*(?:-\s*)?min(?:ute)?s?\b", user_input)
        if words:
            minutes = min(25, int(mins.group(1))) if mins else 4
            max_tokens = max(max_tokens, min(16000, int(minutes * 140 * 1.45) + 1500))
        t0 = time.time()
        for step_no in range(int(c["max_steps"])):
            if kind == "local" and time.time() - t0 > float(c.get("local_timeout_s", 150)) and can_hand_over:
                self._outcome = ("handover", "the local model is taking too long", cost)
                return
            q: asyncio.Queue = asyncio.Queue()

            def on_text(t, q=q):
                loop.call_soon_threadsafe(q.put_nowait, ("text", t))

            def on_think(t, q=q):
                loop.call_soon_threadsafe(q.put_nowait, ("think", t))
            kw = {"max_tokens": max_tokens}
            if kind == "cloud":
                kw.update(model=model, effort=effort)
            call = asyncio.ensure_future(backend.step(system, transcript, tools, on_text, on_think,
                                                      text_only=asked, **kw))
            speech = _OneSpeech([t for t, _ in gathered], words)
            step_text_spoken, fed = False, False
            try:
                while True:
                    getter = asyncio.ensure_future(q.get())
                    done, _ = await asyncio.wait({call, getter}, return_when=asyncio.FIRST_COMPLETED)
                    if getter in done:
                        what, t = getter.result()
                        if what == "think":
                            yield {"type": "thinking", "text": t}
                        else:
                            fed = True
                            for sent in speech.feed(t):
                                spoke = step_text_spoken = True
                                yield {"type": "token", "text": sent + " "}
                        continue
                    getter.cancel()
                    while not q.empty():
                        what, t = q.get_nowait()
                        if what == "think":
                            yield {"type": "thinking", "text": t}
                        else:
                            fed = True
                            for sent in speech.feed(t):
                                spoke = step_text_spoken = True
                                yield {"type": "token", "text": sent + " "}
                    res: Step = call.result()
                    if res.text and not fed:          # a backend that didn't stream: its words still get spoken
                        for sent in speech.feed(res.text):
                            spoke = step_text_spoken = True
                            yield {"type": "token", "text": sent + " "}
                    break
            except BackendError as e:
                logger.warning(f"BRAIN: {kind} failed ({str(e)[:160]})")
                if can_hand_over and not spoke:
                    self._outcome = ("handover", str(e)[:160], cost)
                    return
                self._outcome = ("answer", self._failed_line(kind, str(e), spoke), cost)
                self._replaced = spoke
                return
            except Exception as e:
                from core.claude import ClaudeCut, line_trouble
                if isinstance(e, ClaudeCut) or line_trouble(e):
                    logger.warning(f"BRAIN: lost the line to Claude ({str(e)[:120]})")
                    self._outcome = ("answer", "Sorry, sir, the line to Claude dropped. Ask me again and I'll finish "
                                               "that." if spoke else "I lost the line to Claude for a moment, sir. Could "
                                                                     "you say that again?", cost)
                    self._replaced = True
                    return
                raise
            cost += res.cost_eur
            rec.brain(kind, res.model)
            rec.think(res.thinking)
            transcript.append({"role": "assistant", "text": res.text, "thinking": res.thinking, "calls": res.calls,
                               "raw": res.raw, "backend": kind})
            if not res.calls:
                for sent in speech.flush(False):
                    spoke = step_text_spoken = True
                    yield {"type": "token", "text": sent + " "}
                text = mind.spoken(res.text)
                problem = self.check(text, gathered, user_input, words, False)
                if problem and corrections < int(c.get("corrections", 1)):
                    corrections += 1
                    logger.warning(f"BRAIN: check failed ({problem}) on {text[:200]!r}; asking her to fix it")
                    yield {"type": "thinking", "text": f"\n[check: {problem}]\n"}
                    transcript.append({"role": "user", "text": f"(Before he hears that: {problem} Fix it now: call "
                                                               f"the tool, or say plainly what you did and didn't do.)"})
                    self._replaced = self._replaced or spoke
                    continue
                if problem and can_hand_over:
                    self._outcome = ("handover", f"its answer failed a check: {problem}", cost)
                    return
                if problem:
                    logger.warning(f"BRAIN: answer still fails ({problem}): {text[:200]!r}")
                    text = self._strip_bad(text, gathered) or "I didn't get that done, sir. Tell me again and I'll " \
                                                               "do it properly."
                    self._replaced = True
                elif speech.held and not self._replaced:
                    yield {"type": "token", "text": " ".join(speech.held) + " "}
                if not text:
                    text = "Sorry, sir, I lost my words there. Say that again?"
                logger.info(f"BRAIN: answered after {step_no + 1} step{'s' if step_no else ''} ({res.model}, "
                            f"{time.time() - t0:.1f} s)")
                self._outcome = ("answer", text, cost)
                return
            # ---- tool calls
            for sent in speech.flush(True):
                spoke = True
                yield {"type": "token", "text": sent + " "}
            logger.info(f"BRAIN: step {step_no + 1}: {', '.join(x['name'] for x in res.calls)} ({res.model})")
            results = []
            for call_ in res.calls:
                entry = None
                async for ev in self._tool(call_, user_input, gathered, used, rec, extra, qvec):
                    if ev.get("type") == "_result":
                        entry = ev["entry"]
                        if ev.get("handover") and can_hand_over:
                            self._outcome = ("handover", ev["handover"], cost)
                            return
                        if ev.get("asked"):
                            asked = True
                        if ev.get("model"):
                            model, effort = ev["model"], ev.get("effort")
                        continue
                    yield ev
                if entry:
                    results.append(entry)
            if extra and kind != "cloud":
                tools = self.tools_for("local", user_input, qvec, extra, can_hand_over)
            transcript.extend(results)
        says = [o.get("say") for _, o in gathered if o.get("say")]
        self._outcome = ("answer", " ".join(dict.fromkeys(says)) or "I looked into it, sir, but I need another try.",
                         cost)

    @staticmethod
    def _failed_line(kind: str, why: str, spoke: bool) -> str:
        if kind == "local":
            return (f"My local brain isn't working right now, sir ({why[:90]}). Switch me to Claude in the settings, "
                    f"or check that Ollama is running.")
        return "I can't reach Claude right now, sir. Try again in a moment."

    def _strip_bad(self, text: str, gathered: list) -> str:
        from core.orchestrator_hybrid import claims, unchecked_fact
        ran = [t for t, _ in gathered]
        keep = [s for s in re.split(r"(?<=[.!?])\s+", text) if s and not claims(s) and not _promises(s)
                and not unchecked_fact(s, ran)]
        return " ".join(keep).strip()

    # ------------------------------------------------------------ one tool call
    async def _tool(self, call: dict, user_input: str, gathered: list, used: set, rec, extra: set,
                    qvec) -> AsyncIterator[dict]:
        from core import mind
        orch, belt = self.orch, self.orch.belt
        name, args = call.get("name") or "", dict(call.get("input") or {})

        def result(text: str, error: bool = False, **more) -> dict:
            e = {"role": "tool", "id": call["id"], "name": name, "text": text, "is_error": error}
            if call.get("toolset_name"):
                e["toolset_name"] = call["toolset_name"]
            return {"type": "_result", "entry": {**e, **{k: v for k, v in more.items() if k == "images"}},
                    **{k: v for k, v in more.items() if k != "images"}}

        def step_ev(s: dict) -> dict:
            return {"type": "step", "data": {k: s.get(k) for k in ("id", "tool", "risk", "decision", "ok", "result",
                                                                   "changes", "why")} | {"undo": bool(s.get("undo"))}}
        if call.get("toolset_name") == "browser":
            from core import browser as brw
            res = await asyncio.to_thread(brw.result, {"id": call["id"], "name": name, "input": args,
                                                         "toolset_name": "browser"})
            used.add("browser")
            s = rec.step(f"browser:{name}", args, "free", "her own browser", "ran", {"result": str(res.get("content"))[:300]})
            yield step_ev(s)
            content = res.get("content")
            text = content if isinstance(content, str) else json.dumps(content, default=str)[:4000]
            yield result(text, bool(res.get("is_error")))
            return
        if name == "hand_to_cloud":
            yield result("Claude takes it from here.", handover=str(args.get("why") or "she asked for help")[:160])
            return
        if name == "think_harder":
            c = cfg()
            yield result("A stronger model answers from here on.", model=c["hard_model"], effort="medium")
            return
        if name == "find_tools":
            found = self.rank(str(args.get("need") or user_input), self._all_schemas(), 8, None, qvec)
            for t in found:
                extra.add(t["function"]["name"])
            yield result("Now available: " + "; ".join(f"{t['function']['name']}: "
                                                       f"{(t['function'].get('description') or '')[:120]}" for t in found))
            return
        if name == "undo":
            async for ev in self._undo(args, rec, call["id"]):
                yield ev
            return
        if name in ("make_skill", "run_skill") or name.startswith("skill_"):
            async for ev in self._skill(name, args, user_input, gathered, used, rec, call["id"]):
                yield ev
            return
        if name in HIDDEN or not belt.has(name):
            yield result(f"There's no tool called {name}. find_tools can look for one.", True)
            return
        args = await orch._fill(name, args, user_input)
        level, why = await asyncio.to_thread(risk.judge, name, args, belt, self.approved)
        if level == "ask":
            desc = belt.describe(name, args)
            orch.pending = {"tool": name, "args": args, "desc": desc, "ts": time.time(), "one": True, "why": why}
            s = rec.step(name, args, level, why, "asked")
            yield step_ev(s)
            yield result(f"NOT DONE YET: this needs his yes first, because {why}. In one short sentence of your own, "
                         f"say exactly what you'd do ({desc}) and ask whether you should. Then stop.", asked=True)
            return
        used.add(name)
        shown = False
        async for ev in orch._run(name, args, gathered, ack=False):
            shown = shown or ev.get("type") == "widget"
            yield ev
        out = gathered[-1][1] if gathered else {}
        s = rec.step(name, args, level, why, "approved" if name in self.approved else "ran", out)
        yield step_ev(s)
        text = mind.tool_result_text(out, shown)
        if s.get("undo"):
            text += "\n(Done; it can be undone if he says so.)"
        img = out.get("_image")
        yield result(text, '"error"' in (out.get("result") or ""),
                     **({"images": [{"media_type": img["media_type"], "b64": img["b64"]}]} if img else {}))

    async def _undo(self, args: dict, rec, call_id: str = "") -> AsyncIterator[dict]:
        which = str(args.get("step") or "last").strip()
        step = journal.last_undoable() if which in ("", "last") else journal.find_step(which)[2]
        if not step:
            yield {"type": "_result", "entry": {"role": "tool", "id": call_id, "name": "undo",
                                                "text": "Nothing recent can be undone.", "is_error": True}}
            return
        out = await asyncio.to_thread(journal.undo, step["id"], self.orch.belt.execute)
        s = rec.step("undo", {"step": step["id"]}, "tell", "putting back what she changed", "ran" if out["ok"] else
                     "failed", {"result": json.dumps(out), "say": out.get("say")})
        yield {"type": "step", "data": {"id": s["id"], "tool": "undo", "risk": "tell", "decision": s["decision"],
                                        "ok": out["ok"], "result": out.get("say"), "changes": [], "undo": False}}
        yield {"type": "_result", "entry": {"role": "tool", "id": call_id, "name": "undo",
                                            "text": json.dumps(out), "is_error": not out["ok"]}}

    async def _skill(self, name: str, args: dict, user_input: str, gathered: list, used: set,
                     rec, call_id: str = "") -> AsyncIterator[dict]:
        belt = self.orch.belt

        def entry(text: str, err: bool = False) -> dict:
            return {"type": "_result", "entry": {"role": "tool", "id": call_id, "name": name, "text": text,
                                                 "is_error": err}}
        if name == "make_skill":
            sk = re.sub(r"[^a-z0-9_]", "_", str(args.get("name") or "").lower()).strip("_")[:40] or "my_skill"
            if not sk[:1].isalpha():
                sk = "s_" + sk
            out = await asyncio.to_thread(made.make, sk, str(args.get("purpose") or ""), str(args.get("code") or ""),
                                          args.get("parameters"), args.get("example_args"), belt.execute,
                                          belt.is_action, belt.has)
            s = rec.step("make_skill", {"name": sk, "purpose": args.get("purpose", "")}, "tell",
                         "made a small skill (only planned actions in its dry run)",
                         "ran" if out.get("ok") else "failed", {"result": json.dumps(out, default=str)[:3000]})
            yield {"type": "step", "data": {"id": s["id"], "tool": "make_skill", "risk": "tell",
                                            "decision": s["decision"], "ok": out.get("ok"), "result": sk,
                                            "changes": [], "undo": False}}
            note = ("Dry run OK. Planned actions: " + json.dumps(out.get("would"), default=str)[:1500] +
                    f". Risk: {out.get('risk')} ({out.get('why')}). If that's what he asked, run_skill name={sk}."
                    if out.get("ok") else "It didn't work: " + "; ".join(out.get("problems") or [])[:1500] +
                    ". Fix the code and make_skill again.")
            yield entry(note + f"\nDry-run value: {json.dumps(out.get('value'), default=str)[:1500]}", not out.get("ok"))
            return
        sk = str(args.get("name") or "") if name == "run_skill" else name[len("skill_"):]
        sargs = dict(args.get("args") or {}) if name == "run_skill" else dict(args)
        level, why = risk.judge("run_skill", {"name": sk, "args": sargs}, belt, self.approved)
        if level == "ask" and "run_skill" not in self.approved and name not in self.approved:
            self.orch.pending = {"tool": "run_skill", "args": {"name": sk, "args": sargs},
                                 "desc": f"run my skill {sk.replace('_', ' ')}", "ts": time.time(), "one": True}
            s = rec.step("run_skill", {"name": sk, "args": sargs}, level, why, "asked")
            yield {"type": "step", "data": {"id": s["id"], "tool": f"skill {sk}", "risk": level, "decision": "asked",
                                            "ok": None, "result": why, "changes": [], "undo": False}}
            yield entry(f"NOT DONE YET: it needs his yes because {why}. Ask him in one short sentence of your own.")
            return
        events: list = []

        def record(tool: str, a: dict, lvl: str, w: str, decision: str, out: dict) -> None:
            st = rec.step(tool, a, lvl, w, decision, out, inner=True)
            events.append({"type": "step", "data": {k: st.get(k) for k in ("id", "tool", "risk", "decision", "ok",
                                                                            "result", "changes", "why")}
                           | {"undo": bool(st.get("undo")), "inner": True}})
            if decision == "ran":
                gathered.append((tool, out))

        def judge(tool: str, a: dict):
            return risk.judge(tool, a, belt, self.approved)
        used.add(f"skill_{sk}")
        out = await asyncio.to_thread(made.run, sk, sargs, belt.execute, belt.is_action, belt.has, judge, record)
        for ev in events:
            yield ev
        s = rec.step(f"skill_{sk}", sargs, level, why, "ran" if out.get("ok") else "failed",
                     {"result": json.dumps({k: out.get(k) for k in ("ok", "value", "error")}, default=str)[:2000]})
        yield {"type": "step", "data": {"id": s["id"], "tool": f"skill {sk}", "risk": level, "decision": s["decision"],
                                        "ok": out.get("ok"), "result": str(out.get("value") or out.get("error"))[:300],
                                        "changes": [], "undo": False}}
        yield entry(json.dumps({k: out.get(k) for k in ("ok", "value", "error", "done")}, default=str)[:4000],
                    not out.get("ok"))
