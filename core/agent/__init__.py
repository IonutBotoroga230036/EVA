"""v0.4 "Agent": the task lane. See docs/AGENT.md.

tasks.py    a task: goal, plan, folder, log, consent log, cost; survives restarts
consent.py  free / logged / ask tiers and your standing permissions
pc.py       the PC as a device: LocalPC today, RemotePC when interfaces/pc_agent.py connects
hands.py    what a task can do, each action with its consent kind
runner.py   the loop (Claude + hands), waiting for you, budgets, the one-at-a-time worker
tools.py    the conversation side: task_start / status / answer / pause / resume / cancel, permissions
"""
