---
name: obsidian
description: Write, search, and read notes in the user's Obsidian vault (second brain), including quick notes into today's daily note.
enabled: true
trusted: true
triggers: ["note that", "make a note", "take a note", "my notes", "obsidian", "jot down", "write down", "daily note"]
permissions:
  network: []
  filesystem: ["obsidian.vault_path (from config/settings.yaml)"]
---
Notes are the user's second brain, separate from memory:
- "Note that X", "jot down X" -> obsidian_quick_note (goes into today's daily note).
- A named note or a longer piece ("make a project note about ZippZapp branding") ->
  obsidian_write with a short title and the right folder: Projects, People, Ideas, or Inbox.
- "What did I note about X", "search my notes" -> obsidian_search, then answer from the snippets.
- "Read my note about X" -> obsidian_read.
"Remember that X" is NOT a note; that is the memory skill.
Confirm writes in a few words ("Noted in today's log, sir."). Never invent note contents.
