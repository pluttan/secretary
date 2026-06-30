![Header](header.png)

<div align="center">

# secretary

**A personal AI secretary that keeps you on one project until it ships**

[![License](https://img.shields.io/badge/license-MIT-2C2C2C?style=for-the-badge&labelColor=1E1E1E)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-2C2C2C?style=for-the-badge&logo=python&labelColor=1E1E1E)]()
[![Swift](https://img.shields.io/badge/Swift-tracker-2C2C2C?style=for-the-badge&logo=swift&labelColor=1E1E1E)]()
[![DeepSeek](https://img.shields.io/badge/DeepSeek-agent-2C2C2C?style=for-the-badge&labelColor=1E1E1E)]()
[![Telegram](https://img.shields.io/badge/Telegram-bot-2C2C2C?style=for-the-badge&logo=telegram&labelColor=1E1E1E)]()

</div>

secretary is a personal anti-scatter assistant. A lean macOS tracker reads the title of your focused window — no screenshots, no OCR — and ships it to a host daemon; an agent (Shiki persona, on DeepSeek) judges every fifteen minutes whether you are working on your stated priorities or drifting, and nudges you on Telegram when you wander. On top of the detector sits an orchestrator over openclaw: one command spawns a dedicated, self-onboarding agent for each project, a work-in-progress gate keeps you from spreading thin, and a Twenty CRM mirror tracks every project's stage. Screen-derived data is judged locally and never reaches third parties — the only things that leave the machine are messages to your own Telegram and your own CRM.

## ■ Features

- ❖ **Activity detector (M0)** — a lean Swift tracker reads the focused-window title via Accessibility (no screenshots, no OCR), batches samples to the host; the agent judges work-vs-drift against your `## now` priorities and nudges on Telegram
- ❖ **Privacy by design** — screen-derived data is judged locally and never goes to third parties; the only outbound channels are your own Telegram and your own CRM
- ❖ **Menu-bar kill-switch** — pause tracking for 10 minutes from the macOS menu bar; the mac goes fully blind (reads nothing, sends nothing) and auto-resumes — off-forever is deliberately impossible
- ❖ **Project orchestrator** — a single command spawns a dedicated, self-onboarding agent per project (repo + Twenty CRM Track + workspace); the new agent interviews you for scope and monetization
- ❖ **WIP gate (anti-scatter)** — activating a project is gated by a configurable work-in-progress limit; "freeze something first" before you start another
- ❖ **CRM ↔ bot sync** — one call keeps the Twenty Track stage and the bot's `## now` nag-list consistent, with rollback on desync
- ❖ **Conversation hand-off** — hand the chat to a project agent (it answers in the same thread) and take it back via stop-words
- ❖ **Aspect engines** — diary, life-log, money-path, last-mile, planner, reports, vent — content is self-collected through dialogue, never hard-coded
- ❖ **Project epitaphs** — killing a project archives its workspace and writes a graveyard note instead of deleting it

## ■ Stack

| Component | Technology |
|-----------|------------|
| Detector | Swift tracker (Accessibility, no screenshot) + Python 3.13 daemon on the host |
| Agent | openclaw orchestrator with a DeepSeek-backed persona |
| CRM | Twenty (self-hosted) over GraphQL |
| Menu bar | Hammerspoon (Lua) tracking toggle |
| Scheduling | systemd user timers |
| Messaging | Telegram Bot API |
| Engines | Python (stdlib) exec-style spawn modules |

## ■ How It Works

```
1. mac tracker samples the focused-window title every 5s, batches to the host every 5 min
2. the agent (15-min tick) reads the title digest + your `## now` priorities
3. it judges work vs drift; on drift it nudges you on Telegram in its own voice
4. you run projects through the orchestrator: spawn agent -> WIP-gate -> CRM sync
5. screen-derived data stays local; only Telegram and the CRM leave the machine
```

## ■ Screenshots

![Screenshot](screenshots/main.png)

## ■ Usage

```bash
# host daemon + 15-min judge/companion tick (pcomp)
systemctl --user enable --now shiki-companion.timer

# mac tracker
swiftc -O tracker/main.swift -o tracker/m0tracker
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pluttan.m0tracker.plist

# orchestrator tools (invoked from chat):
#   create_project, set_project_stage, set_project_money,
#   prioritize, crm_bot_sync, handoff_to_agent / return_to_main
```

## ■ License

MIT © [pluttan](https://github.com/pluttan)
