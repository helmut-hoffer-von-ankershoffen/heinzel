---
name: explain-heinzel
description: Explain what Heinzelmännchen (hz) can do on this machine - its modules, plugins, connectors and Heinzelmännchen - from what is installed, not from memory. Use when someone asks what hz is, what it can do, or where to start.
---

# Explain what hz can do

Answer from what is installed here, in plain words, and end with one command worth running next.

1. Read what is there, with `--json` where you run commands yourself:
   - `hz system info` — version, plugins and their errors.
   - `hz system modules` — every module and what it offers.
   - `hz system connector list` — which tools (trackers, chat, documents) are connected.
   - `hz system skill list` — the skills plugins ship, like this one.
2. Group what you found by what it does for the person, not by module name: *knowing what the team is doing*,
   *drafting* (notes, updates, release notes), *reminders and schedules*, *connections to their tools*.
3. Say what is missing and the one command that adds it, e.g. a connector that isn't signed in, or a plugin the
   team's layer recommends (`hz system doctor` names both).
4. Keep it short: three to six bullets and one next step. Never invent a command; `hz <module> --help` lists them.

What hz never does on its own: it doesn't send, post or publish without the person confirming, and it runs no
script from a skill.
