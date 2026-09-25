"""A fake Google account with the same methods as core.google_api.GoogleClient. It has NO send method."""

from datetime import datetime


class FakeGoogle:
    def __init__(self, events=None, messages=None, contacts=None):
        self.events = list(events or [])
        self.messages = list(messages or [])
        self.contacts = set(contacts or [])
        self.inserted, self.deleted, self.drafts = [], [], []

    def list_events(self, start, end, max_results=25):
        return [e for e in self.events if e["start"] and start <= e["start"] < end][:max_results]

    def insert_event(self, title, start, end, description=""):
        self.inserted.append((title, start, end))
        return {"id": "new"}

    def delete_event(self, event_id):
        self.deleted.append(event_id)

    def list_messages(self, query, max_results=5):
        import re
        words = re.sub(r"\b(is:unread|in:inbox|in:sent|newer_than:\w+)\b", "", query).replace("from:", "").split()
        hits = [m for m in self.messages
                if not words or any(w.lower() in (m["from"] + m["subject"] + m["snippet"]).lower() for w in words)]
        return len(hits), hits[:max_results]

    def sent_recipients(self, max_results=100):
        return set(self.contacts)

    def get_body(self, message_id):
        return next(m.get("body", m["snippet"]) for m in self.messages if m["id"] == message_id)

    def create_draft(self, to, subject, body, thread_id="", in_reply_to=""):
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id,
                            "in_reply_to": in_reply_to})
        return "d1"


def ev(id, title, start, end=None, all_day=False):
    return {"id": id, "title": title, "start": start, "end": end or start, "all_day": all_day, "location": ""}


def msg(id, frm, subject, snippet="", body="", labels=None, bulk=False, to="ionut@gmail.com"):
    return {"id": id, "thread_id": f"t{id}", "from": frm, "subject": subject, "date": "", "message_id": f"<{id}@x>",
            "snippet": snippet, "body": body or snippet, "labels": labels or [], "bulk": bulk, "to": to,
            "in_reply_to": ""}
