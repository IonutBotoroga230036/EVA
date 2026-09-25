---
name: forge
description: Build a brand-new skill for yourself when the user asks for an ability you don't have, then install or discard it on their approval.
enabled: true
trusted: true
triggers: ["build a skill", "build it", "learn to", "teach yourself", "new skill", "forge"]
permissions:
  network: ["api.anthropic.com"]
  filesystem: ["skills/_forge", "skills/<new skill>", "data/forge"]
---
Use forge_build when the user asks you to build, make, or learn a new ability. It drafts the
skill with Claude, reviews the code, and runs its tests; nothing is installed yet. Then say what
the skill does and ask whether to install it. forge_install only after a clear yes;
forge_discard when they decline. Never claim a skill is installed unless forge_install succeeded.
