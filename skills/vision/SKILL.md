---
name: vision
description: Look at the user's screen and describe or read what is on it, or answer a question about it.
enabled: true
trusted: true
triggers: ["screen", "what am i looking at", "look at this", "read this"]
permissions:
  network: ["localhost:11434"]
  filesystem: ["data/screenshots"]
---
Use see_screen when the user asks about anything visible on their screen: what an error
says, what is in a window, reading text, or checking their code. Pass their question as
`question`. Answer from the description only; if it is unclear, say what you can see and
what you can't. Screenshots stay on this machine.
