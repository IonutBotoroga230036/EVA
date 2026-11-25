# v0.5: one mind and the workbench

## One mind

Before v0.5 every sentence went the same way: a quick model (Claude Haiku, or Jev as backup) picked ONE tool, the
tool ran, and E.V.A. read out the tool's own sentence. When nothing fitted, a small local model chatted back.
Nothing in that path could think "he wants to look at an STL; nothing on his PC opens STL files; I could show it
here or install a viewer". That is why she felt stiff.

Now:

```
"lights off" / "next train to Nijmegen" / "remind me at 5"
    -> fast path or understanding (Haiku, Jev, local) picks the tool -> it runs -> her line   (as before)

"how can I see the files?" / "should I print it at the library?" / "what would you do?" / vague / several steps
    -> understanding says work_it_out, or no tool ran, and it isn't personal chat
    -> the mind (core/mind.py): Claude Haiku 5.5 with every tool, the conversation, the situation (time, open
       tasks, what the last task made, focus), what you told her before; think_harder switches to Sonnet
    -> she looks things up, checks your PC (check_installed), offers options, asks one question,
       opens files (open_file), installs apps after your yes (install_app), starts tasks for big jobs
```

| Part | Where |
|---|---|
| The loop | `HybridOrchestrator._mind` (core/orchestrator_hybrid.py): up to `mind.max_steps` tool rounds |
| Instructions, tools, situation | `core/mind.py` (the instructions never change within a session, so the cache holds) |
| Hands on the PC | `core/pc_tools.py`: check_installed, install_app (always asks), open_file (task files only, documents only) |
| Handing over | the `work_it_out` tool in understanding's list; Jev and Haiku can pick it |
| Chat stays chat | feelings, herself, "let's talk" still go to the conversation lane (`mind.is_chat`) |

Safety is unchanged: anything that acts goes through "Just to confirm, sir" (the turn ends there, your yes runs
it); the fact guard (no calendar, email or weather facts without a check) and the claim guard (no "done" without
an action) apply to her answers too. Local only, no key or no budget: the old way, exactly as before. Off:
`mind.enabled: false`, or "use local for the mind".

Cost: Haiku 5.5 is $0.10 per million input tokens; one thought with all tools is a fraction of a cent (the tool
list is cached). Sonnet only when she decides it's hard.

## The workbench

While a task works, the window shows a live stage (`core/agent/live.py` -> PULSE `agent.live` -> `task_live` to
every window):

- the plan, ticking off; what she's doing right now ("Writing stand.py", "Looking at assembly.stl")
- the activity feed, with what she says between steps
- tabs: Model (each new version turning in 3D as soon as she makes it), Code (what she's writing), Output (what it
  printed, red when it failed)
- a scanning line while she works; status Working / Needs you / Done

It opens by itself when a task starts (close it and a small pill at the top keeps showing what she's doing; the
wrench button brings it back). `/stage` is the same thing full screen, for a second monitor and later the desk
screen. A window that opens mid-task loads everything so far (`GET /api/tasks/live`, `GET /api/tasks/{id}/live`).
Nothing on the workbench is spoken; she still speaks only at milestones, questions and the result.
