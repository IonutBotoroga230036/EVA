---
name: memory
description: Remember, recall, and forget facts about the user, and save standing instructions ("from now on...").
enabled: true
trusted: true
triggers: ["remember", "forget", "from now on", "what do you know about me", "do you remember", "delete that"]
permissions:
  network: []
  filesystem: ["data/cortex.db", "EVA.md"]
---
Use remember_fact when the user explicitly asks you to remember something about them.
Use add_instruction when the user sets a rule for how you should behave in the future
("from now on", "going forward", "always", "never"). Use recall_memory when they ask what
you know or remember, or what they told you about something: it searches facts, past
conversations, and notes together. Use forget_memory when they ask you to forget one thing, and
forget_recent_facts when they say to forget what you just remembered or learned.
Confirm in a few words ("Noted, sir."). When recalling, answer only from the returned
facts and never invent memories.
