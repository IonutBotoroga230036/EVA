---
name: think
description: Think hard about a difficult question with Claude's extended reasoning, when the user asks you to think carefully, reason something through, or help with a hard decision.
enabled: true
trusted: true
triggers: ["think hard", "think carefully", "think deeply", "think about", "deep think", "ask claude", "help me decide"]
permissions:
  network: ["api.anthropic.com"]
  filesystem: []
---
Use think_deeply for questions that need careful reasoning: decisions, plans, trade-offs,
explanations of hard topics, checking an argument. Not for facts a search answers or small talk.
The answer comes back ready to speak; say it as it is.
