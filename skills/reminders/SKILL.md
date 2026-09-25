---
name: reminders
description: Reminders, snooze, do-not-disturb, and the daily briefing (ORACLE).
enabled: true
trusted: true
triggers: ["remind me", "reminder", "snooze", "do not disturb", "good morning", "briefing"]
permissions:
  network: []
  filesystem: ["data/reminders.json"]
---
set_reminder for "remind me to X at/in/tomorrow...". list_reminders, cancel_reminder.
snooze_reminder right after a reminder was spoken. do_not_disturb holds everything except
nothing is lost: held reminders are delivered after. morning_briefing for "good morning".
