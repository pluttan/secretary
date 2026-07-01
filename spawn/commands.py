#!/usr/bin/env python3.13
# commands.py — slash-command dispatcher for the secretary bot (@shikipassmacbot). Maps /board,
# /comms, /day, … to their engines and replies in telegram. Registered via setMyCommands so they
# appear in the bot's command menu. Message updates are handed here by the shared poll. /help prints
# the full map (in-telegram documentation). Reuses menu.py section formatters. Author: pluttan

import json
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact
import secret
import menu

SECRETARY = Path.home() / "secretary"
SPAWN = SECRETARY / "spawn"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))

# (command, description, kind) — kind: menu-section fn / special
CMDS = [
    ("menu", "меню-хаб со всеми разделами"),
    ("board", "канбан-доска (проекты/задачи)"),
    ("comms", "переписки: долги, обещания, дедлайны"),
    ("debt", "кому не ответил (рабочий тг)"),
    ("day", "якоря на сегодня"),
    ("hours", "золотые часы (энергопрофиль)"),
    ("routines", "рутины (что не сделано)"),
    ("rem", "активные напоминания"),
    ("year", "год активности картинкой"),
    ("invest", "инвест-баланс"),
    ("guard", "инфра: диск/бэкап/мак"),
    ("help", "список всех команд"),
]


def _tg(method, **fields):
    try:
        token = secret.get("telegram-bot-token")
    except Exception:
        return None
    cfg = [f'url = "https://api.telegram.org/bot{token}/{method}"']
    for k, v in fields.items():
        if k in ("text", "caption"):
            v = redact.redact(v)
        cfg.append(f'data = "{k}={quote(str(v), safe="")}"')
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "de-german",
                            "curl -s --max-time 20 -K -"],
                           input="\n".join(cfg) + "\n", capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        print(f"[cmd] tg {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _send(chat, text):
    _tg("sendMessage", chat_id=chat, text=text)


def help_text():
    lines = ["секретарь — команды (@shikipassmacbot):\n"]
    lines += [f"/{c}  — {d}" for c, d in CMDS]
    lines.append("\n(разговором отвечает Шики через отдельный openclaw-бот)")
    return "\n".join(lines)


def handle_message(msg):
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text[1:].split()[0].split("@")[0].lower()
    chat = msg.get("chat", {}).get("id")

    if cmd in ("start", "help"):
        _send(chat, help_text())
    elif cmd == "menu":
        menu.show()
    elif cmd == "board":
        import board
        board.show()
    elif cmd == "year":
        _send(chat, "▤ собираю год-картинку…")
        subprocess.run(["python3.13", str(SPAWN / "reports_img.py"), "year"], timeout=200)
    elif cmd == "comms":
        _send(chat, menu.sec_comms())
    elif cmd == "debt":
        d = menu._run_json("comms.py", "debt") or {}
        rows = d.get("debt", [])
        body = "\n".join(f"  {x['age_days']}д — {x['who'][:26]}: {x['last'][:40]}" for x in rows[:10])
        _send(chat, f"кому не ответил ({d.get('count', 0)}):\n{body or '  чисто'}")
    elif cmd == "day":
        _send(chat, menu.sec_day())
    elif cmd == "hours":
        _send(chat, menu.sec_hours())
    elif cmd == "routines":
        _send(chat, menu.sec_routines())
    elif cmd == "rem":
        _send(chat, menu.sec_rem())
    elif cmd == "invest":
        _send(chat, menu.sec_invest())
    elif cmd == "guard":
        _send(chat, menu.sec_infra())
    else:
        _send(chat, f"неизвестная команда /{cmd}. /help — список")
    return True


def register():
    """Publish the command list to Telegram (shows in the bot's menu button)."""
    cmds = [{"command": c, "description": d} for c, d in CMDS]
    return _tg("setMyCommands", commands=json.dumps(cmds, ensure_ascii=False))


def main():
    a = sys.argv[1:]
    if a and a[0] == "register":
        print(json.dumps(register(), ensure_ascii=False)); return
    if a and a[0] == "help":
        print(help_text()); return
    print(json.dumps({"usage": "register|help"}))


if __name__ == "__main__":
    main()
