---
name: email
description: Check, search, and read the user's Gmail, and draft replies that wait in Gmail drafts (SCRIBE). Never sends.
enabled: true
trusted: true
triggers: ["email", "emails", "inbox", "gmail", "mail", "reply to", "draft"]
permissions:
  network: ["gmail.googleapis.com", "oauth2.googleapis.com", "localhost:11434"]
  filesystem: ["data/google/token.json"]
---
email_unread for "check my email / any new emails". email_search for "emails from Tom",
"emails about Deloitte". email_read to read one aloud or summarise it (answer briefly from its text).
email_draft to write an email or a reply: it lands in Gmail drafts and is NEVER sent by you.
