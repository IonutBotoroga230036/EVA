"""
Google account access for Calendar (TEMPO) and Gmail (SCRIBE), with ONE sign-in.

Setup once (see docs/GOOGLE_SETUP.md): a Google Cloud project with the Calendar and Gmail
APIs enabled and an OAuth client of type "Desktop app". Save its JSON OUTSIDE the repo,
at the path in settings (google.credentials_path). Then either run
    python -m core.google_api
or just ask E.V.A. about your calendar: she opens the sign-in page herself.

Scopes are the least she needs: read and change calendar events, read mail, and create
drafts. She cannot send mail; every draft waits in Gmail for you.
The token is stored in data/google/token.json (git-ignored) and refreshed automatically.
"""

from __future__ import annotations

import base64
import threading
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from loguru import logger

SCOPES = ["https://www.googleapis.com/auth/calendar.events",
          "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]
TOKEN = Path("data/google/token.json")


class NeedAuth(RuntimeError):
    """Raised when there is no usable Google sign-in yet."""


def _cfg() -> dict:
    from core.settings import get_settings
    g = get_settings().get("google", {})
    return {"credentials": Path(g.get("credentials_path", "../secrets/gcp-oauth.keys.json")).expanduser(),
            "timezone": g.get("timezone", "Europe/Amsterdam")}


def credentials_present() -> bool:
    return _cfg()["credentials"].exists()


def load_creds():
    if not TOKEN.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:
            logger.warning(f"GOOGLE: token refresh failed ({e}); a new sign-in is needed")
    return None


def authorize_interactive():
    """Opens the browser for consent and waits. Run from a terminal or a background thread."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(_cfg()["credentials"]), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    global _client
    _client = None
    logger.info("GOOGLE: signed in")
    return creds


_auth_lock = threading.Lock()


def start_auth_background() -> bool:
    """Kick off the browser sign-in without blocking the conversation. False if already running."""
    if not _auth_lock.acquire(blocking=False):
        return False

    def run():
        try:
            authorize_interactive()
        except Exception as e:
            logger.error(f"GOOGLE: sign-in failed: {e}")
        finally:
            _auth_lock.release()
    threading.Thread(target=run, daemon=True, name="google-auth").start()
    return True


def auth_message() -> str:
    """What E.V.A. says when Google isn't connected, and she starts fixing it if she can."""
    if not credentials_present():
        return ("Google isn't set up yet, sir. Follow docs/GOOGLE_SETUP.md to create the credentials file, "
                "then ask me again.")
    started = start_auth_background()
    return ("I need to connect to your Google account first, sir. I've opened the sign-in page in your browser. "
            "Approve it, then ask me again." if started else
            "The Google sign-in page is already open, sir. Approve it, then ask me again.")


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.astimezone()).isoformat()


def _when(e: dict, key: str) -> tuple[Optional[datetime], bool]:
    v = e.get(key, {})
    if "dateTime" in v:
        return datetime.fromisoformat(v["dateTime"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None), False
    if "date" in v:
        return datetime.fromisoformat(v["date"]), True
    return None, False


class GoogleClient:
    """The few Google calls E.V.A. makes. Tests replace it with a fake that has the same methods."""

    def __init__(self, creds):
        from googleapiclient.discovery import build
        self.cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.tz = _cfg()["timezone"]

    # ---------------- calendar
    def list_events(self, start: datetime, end: datetime, max_results: int = 25) -> list[dict]:
        items = self.cal.events().list(calendarId="primary", timeMin=_iso(start), timeMax=_iso(end),
                                       singleEvents=True, orderBy="startTime", maxResults=max_results
                                       ).execute().get("items", [])
        out = []
        for e in items:
            s, all_day = _when(e, "start")
            en, _ = _when(e, "end")
            out.append({"id": e["id"], "title": e.get("summary", "(no title)"), "start": s, "end": en,
                        "all_day": all_day, "location": e.get("location", "")})
        return out

    def insert_event(self, title: str, start: datetime, end: datetime, description: str = "") -> dict:
        body = {"summary": title, "description": description,
                "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": self.tz},
                "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": self.tz}}
        return self.cal.events().insert(calendarId="primary", body=body).execute()

    def delete_event(self, event_id: str) -> None:
        self.cal.events().delete(calendarId="primary", eventId=event_id).execute()

    # ---------------- gmail
    def list_messages(self, query: str, max_results: int = 5) -> tuple[int, list[dict]]:
        res = self.gmail.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        out = []
        for m in res.get("messages", []):
            full = self.gmail.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "To", "Cc", "Subject", "Date", "Message-ID", "List-Unsubscribe",
                                 "List-Id", "Precedence", "In-Reply-To"]).execute()
            h = {x["name"].lower(): x["value"] for x in full.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "thread_id": full.get("threadId"), "from": h.get("from", ""),
                        "to": h.get("to", ""), "cc": h.get("cc", ""),
                        "subject": h.get("subject", "(no subject)"), "date": h.get("date", ""),
                        "message_id": h.get("message-id", ""), "snippet": full.get("snippet", ""),
                        "labels": full.get("labelIds", []),
                        "bulk": bool(h.get("list-unsubscribe") or h.get("list-id")
                                     or h.get("precedence", "").lower() in ("bulk", "list", "junk")),
                        "in_reply_to": h.get("in-reply-to", "")})
        return int(res.get("resultSizeEstimate", len(out))), out

    def sent_recipients(self, max_results: int = 100) -> set[str]:
        """Addresses you've written to in the last half year: the people who matter to you."""
        res = self.gmail.users().messages().list(userId="me", q="in:sent newer_than:180d",
                                                 maxResults=max_results).execute()
        out: set[str] = set()
        from email.utils import getaddresses
        for m in res.get("messages", []):
            full = self.gmail.users().messages().get(userId="me", id=m["id"], format="metadata",
                                                     metadataHeaders=["To", "Cc"]).execute()
            for x in full.get("payload", {}).get("headers", []):
                out |= {a.lower() for _, a in getaddresses([x["value"]]) if a}
        return out

    def get_body(self, message_id: str) -> str:
        msg = self.gmail.users().messages().get(userId="me", id=message_id, format="full").execute()

        def walk(part):
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "ignore")
            for p in part.get("parts", []) or []:
                t = walk(p)
                if t:
                    return t
            return ""
        return walk(msg.get("payload", {})) or msg.get("snippet", "")

    def create_draft(self, to: str, subject: str, body: str, thread_id: str = "", in_reply_to: str = "") -> str:
        mime = MIMEText(body)
        mime["to"], mime["subject"] = to, subject
        if in_reply_to:
            mime["In-Reply-To"] = mime["References"] = in_reply_to
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        msg = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        return self.gmail.users().drafts().create(userId="me", body={"message": msg}).execute().get("id", "")


_client: Optional[GoogleClient] = None


def get_google() -> GoogleClient:
    global _client
    if _client is None:
        creds = load_creds()
        if not creds:
            raise NeedAuth("no Google sign-in")
        _client = GoogleClient(creds)
    return _client


def connected() -> bool:
    try:
        get_google()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if not credentials_present():
        print(f"Put your OAuth client JSON at {_cfg()['credentials']} first (see docs/GOOGLE_SETUP.md).")
    else:
        authorize_interactive()
        print("Signed in. E.V.A. can now use your calendar and Gmail.")
