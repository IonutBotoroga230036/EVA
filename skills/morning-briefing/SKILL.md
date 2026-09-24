---
name: morning-briefing
description: Give a short morning briefing with the date, time, and weather when the user says good morning or asks for a briefing.
enabled: true
trusted: true
triggers: ["good morning", "morning briefing", "brief me", "daily briefing"]
permissions:
  network: []
  filesystem: []
---
For a briefing, gather data in this order: get_datetime first, then get_weather for the
user's home city. Calendar and email are not connected yet, so skip them silently rather
than mentioning them. Then speak one compact briefing of at most three sentences, for
example: "Good morning, sir. It's Friday the 25th, 08:10. Twelve degrees and cloudy in
Breda, rain likely after three."
