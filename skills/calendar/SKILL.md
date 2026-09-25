---
name: calendar
description: Read the user's Google Calendar, find free time, and add or remove events (TEMPO).
enabled: true
trusted: true
triggers: ["calendar", "schedule", "agenda", "what am i doing", "meeting", "free time", "opening"]
permissions:
  network: ["www.googleapis.com", "oauth2.googleapis.com"]
  filesystem: ["data/google/token.json"]
---
calendar_agenda for "what's on my calendar / what am I doing tomorrow / this week".
calendar_free for "any opening / when am I free / do I have time for X".
calendar_add to create an event: title, day, time, duration. It asks the user first.
calendar_delete to remove one: it asks first. Speak the results exactly as returned.
