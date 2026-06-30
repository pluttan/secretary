![Header](header.png)

<div align="center">

# secretary

**Персональный ИИ-секретарь, который держит тебя на одном проекте, пока он не доведён**

[![License](https://img.shields.io/badge/license-MIT-2C2C2C?style=for-the-badge&labelColor=1E1E1E)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-2C2C2C?style=for-the-badge&logo=python&labelColor=1E1E1E)]()
[![Swift](https://img.shields.io/badge/Swift-tracker-2C2C2C?style=for-the-badge&logo=swift&labelColor=1E1E1E)]()
[![DeepSeek](https://img.shields.io/badge/DeepSeek-agent-2C2C2C?style=for-the-badge&labelColor=1E1E1E)]()
[![Telegram](https://img.shields.io/badge/Telegram-bot-2C2C2C?style=for-the-badge&logo=telegram&labelColor=1E1E1E)]()

</div>

secretary — персональный помощник против распыления. Лёгкий трекер на macOS читает заголовок активного окна — без скриншотов и OCR — и шлёт его демону на хосте; агент (персона Шики, на DeepSeek) каждые пятнадцать минут судит, работаешь ли ты по заявленным приоритетам или дрейфуешь, и подталкивает в Telegram, когда заносит не туда. Поверх детектора — оркестратор на openclaw: одна команда рождает выделенного самоонбордящегося агента под каждый проект, WIP-гейт не даёт расползаться, а зеркало в Twenty CRM ведёт стадию каждого проекта. Данные с экрана судятся локально и не уходят третьим лицам — машину покидают только сообщения в твой собственный Telegram и твою собственную CRM.

## ■ Features

- ❖ **Детектор активности (M0)** — лёгкий Swift-трекер читает заголовок активного окна через Accessibility (без скриншотов и OCR), шлёт замеры пачками на хост; агент судит работу-или-дрейф по приоритетам `## now` и душнит в Telegram
- ❖ **Приватность by design** — данные с экрана судятся локально и не уходят третьим лицам; единственные внешние каналы — твой Telegram и твоя CRM
- ❖ **Рубильник в menu bar** — пауза трекинга на 10 минут из строки меню macOS; мак слепнет целиком (ничего не читает и не шлёт) и сам возобновляется — выключить навсегда намеренно нельзя
- ❖ **Оркестратор проектов** — одна команда рождает выделенного самоонбордящегося агента под проект (репо + Track в Twenty CRM + workspace); новый агент сам расспрашивает про подноготную и монетизацию
- ❖ **WIP-гейт (анти-распыление)** — активация проекта гейтится настраиваемым лимитом одновременной работы: «заморозь что-то прежде», чем начать новое
- ❖ **Синк CRM ↔ бот** — один вызов держит стадию Track в Twenty и наг-лист `## now` бота в согласии, с откатом при рассинхроне
- ❖ **Передача разговора** — отдать чат агенту проекта (он отвечает в том же треде) и забрать обратно по стоп-словам
- ❖ **Аспектные движки** — дневник, лайф-трекинг, money-path, last-mile, planner, reports, vent — контент собирается диалогом, не зашит
- ❖ **Эпитафии проектов** — убийство проекта архивирует его workspace и пишет надгробную заметку, а не стирает

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
1. трекер на маке снимает заголовок активного окна каждые 5с, шлёт пачкой на хост раз в 5 мин
2. агент (тик раз в 15 мин) читает дайджест заголовков + твои приоритеты `## now`
3. судит работу против дрейфа; на дрейфе душнит в Telegram своим голосом
4. проекты ведутся через оркестратор: рождение агента -> WIP-гейт -> синк с CRM
5. данные с экрана остаются локально; машину покидают только Telegram и CRM
```

## ■ Screenshots

![Screenshot](screenshots/main.png)

## ■ Usage

```bash
# демон на хосте + 15-минутный тик судьи/компаньона (pcomp)
systemctl --user enable --now shiki-companion.timer

# трекер на маке
swiftc -O tracker/main.swift -o tracker/m0tracker
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pluttan.m0tracker.plist

# инструменты оркестратора (вызываются из чата):
#   create_project, set_project_stage, set_project_money,
#   prioritize, crm_bot_sync, handoff_to_agent / return_to_main
```

## ■ License

MIT © [pluttan](https://github.com/pluttan)
