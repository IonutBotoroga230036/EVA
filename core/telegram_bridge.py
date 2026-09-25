"""
Telegram channel: E.V.A. on your phone, screen off, anywhere.

Setup (2 minutes): in Telegram, talk to @BotFather, send /newbot, pick a name. Put the token in
config/secrets.env as TELEGRAM_BOT_TOKEN=... and restart E.V.A. The terminal prints a pairing code;
send "/pair <code>" to your bot. From then on ONLY your Telegram account can talk to her.

- Long polling: outbound calls to api.telegram.org only, no open ports, no webhook.
- Private chats only; everything else is ignored and logged.
- Each chat gets its own conversation with the same memory, skills, and safety gates.
- Confirmations ("Shall I go ahead?") come with Yes / No buttons.
- ORACLE pushes reminders, meeting heads-ups, and important mail here (settings telegram.proactive).
- Voice notes are transcribed with faster-whisper when it's installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from loguru import logger

API = "https://api.telegram.org"
STATE = Path("data/telegram.json")
MAX_LEN = 4000


def load_token() -> Optional[str]:
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return os.environ["TELEGRAM_BOT_TOKEN"]
    try:
        from core.security.vault import Vault
        v = Vault()
        v.load()
        return v.get_optional("TELEGRAM_BOT_TOKEN")
    except Exception:
        return None


class TelegramBridge:
    def __init__(self, token: str, allowed_ids: Optional[set[int]] = None,
                 orch_factory: Optional[Callable] = None, http: Optional[httpx.AsyncClient] = None,
                 state_path: Path = STATE, poll_timeout: int = 25):
        self.token = token
        self.state_path = state_path
        self.allowed: set[int] = set(allowed_ids or set()) | set(self._load_state().get("paired", []))
        self.pair_code: Optional[str] = None
        self.pair_expires = 0.0
        self.http = http or httpx.AsyncClient(timeout=poll_timeout + 10)
        self.poll_timeout = poll_timeout
        self.offset = 0
        self._orch_factory = orch_factory
        self._orchs: dict[int, object] = {}
        self._stop = False
        if not self.allowed:
            self.new_pair_code()

    # ------------------------------------------------------------ state and pairing
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"paired": sorted(self.allowed)}), encoding="utf-8")

    def new_pair_code(self) -> str:
        self.pair_code = f"{random.SystemRandom().randint(0, 999999):06d}"
        self.pair_expires = time.time() + 15 * 60
        logger.warning(f"TELEGRAM: to link your phone, send  /pair {self.pair_code}  to your bot (valid 15 minutes)")
        return self.pair_code

    # ------------------------------------------------------------ Telegram API
    async def call(self, method: str, **params) -> dict:
        r = await self.http.post(f"{API}/bot{self.token}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method}: {data.get('description')}")
        return data.get("result", {})

    async def send(self, chat_id: int, text: str, buttons: bool = False) -> None:
        chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
        for i, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk}
            if buttons and i == len(chunks) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": "Yes, go ahead", "callback_data": "yes"},
                                                              {"text": "No", "callback_data": "no"}]]}
            await self.call("sendMessage", **params)

    async def notify(self, text: str, widget: Optional[dict] = None) -> bool:
        """ORACLE delivery. True if at least one paired phone got it."""
        ok = False
        for uid in self.allowed:
            try:
                await self.send(uid, text)
                ok = True
            except Exception as e:
                logger.warning(f"TELEGRAM: push failed: {e}")
        return ok

    # ------------------------------------------------------------ conversation
    def _orch(self, chat_id: int):
        if chat_id not in self._orchs:
            if self._orch_factory:
                self._orchs[chat_id] = self._orch_factory(f"tg-{chat_id}")
            else:
                from core.orchestrator_hybrid import HybridOrchestrator
                self._orchs[chat_id] = HybridOrchestrator(session_id=f"tg-{chat_id}")
        return self._orchs[chat_id]

    async def _transcribe(self, file_id: str) -> Optional[str]:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
        info = await self.call("getFile", file_id=file_id)
        r = await self.http.get(f"{API}/file/bot{self.token}/{info['file_path']}")
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(r.content)
            path = f.name
        try:
            def run():
                model = WhisperModel("small", device="cpu", compute_type="int8")
                segs, _ = model.transcribe(path, beam_size=1)
                return " ".join(s.text.strip() for s in segs).strip()
            return await asyncio.to_thread(run)
        finally:
            os.unlink(path)

    async def handle_text(self, chat_id: int, text: str) -> None:
        await self.call("sendChatAction", chat_id=chat_id, action="typing")
        final, confirm = "", False
        async for ev in self._orch(chat_id).process_stream(text):
            if ev["type"] == "widget" and ev.get("data", {}).get("kind") == "confirm":
                confirm = True
            elif ev["type"] == "final":
                final = ev["text"]
        logger.info(f"TELEGRAM EVA: {final}")
        await self.send(chat_id, final or "I had trouble with that one, sir.", buttons=confirm)

    async def handle_update(self, upd: dict) -> None:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            uid = cq.get("from", {}).get("id")
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
            if uid in self.allowed and cq.get("data") in ("yes", "no"):
                await self.handle_text(cq["message"]["chat"]["id"], cq["data"])
            return
        msg = upd.get("message") or {}
        chat, uid = msg.get("chat", {}), msg.get("from", {}).get("id")
        if not msg or chat.get("type") != "private":
            return
        text = (msg.get("text") or "").strip()
        if uid not in self.allowed:
            if text.startswith("/pair") and self.pair_code and time.time() < self.pair_expires \
                    and text.split()[-1] == self.pair_code:
                self.allowed.add(uid)
                self.pair_code = None
                self._save_state()
                logger.info(f"TELEGRAM: paired with user {uid}")
                await self.send(chat["id"], "Paired, sir. I'll answer here and send you reminders and alerts.")
            else:
                logger.warning(f"TELEGRAM: ignored a message from unpaired user {uid}")
            return
        if msg.get("voice"):
            heard = await self._transcribe(msg["voice"]["file_id"])
            if heard is None:
                await self.send(chat["id"], "Voice notes need Whisper on the PC, sir: pip install faster-whisper")
                return
            text = heard
            logger.info(f"TELEGRAM (voice): {text}")
        if text and not text.startswith("/"):
            logger.info(f"TELEGRAM USER: {text}")
            await self.handle_text(chat["id"], text)

    async def poll_once(self) -> int:
        updates = await self.call("getUpdates", offset=self.offset, timeout=self.poll_timeout,
                                  allowed_updates=["message", "callback_query"])
        for upd in updates:
            self.offset = max(self.offset, upd["update_id"] + 1)
            try:
                await self.handle_update(upd)
            except Exception as e:
                logger.exception(f"TELEGRAM: update failed: {e}")
        return len(updates)

    async def run(self) -> None:
        try:
            me = await self.call("getMe")
            logger.info(f"TELEGRAM: online as @{me.get('username')}")
        except Exception as e:
            logger.error(f"TELEGRAM: token rejected ({e}); channel disabled")
            return
        backoff = 1
        while not self._stop:
            try:
                await self.poll_once()
                backoff = 1
            except Exception as e:
                logger.warning(f"TELEGRAM: poll failed ({e}); retrying in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self) -> None:
        self._stop = True
