#!/usr/bin/env python3.13
# board.py — the kanban OVERLAY over all the secretary's mechanisms (yougileTgBot-style board,
# but aggregating, not a new store). Pulls cards from the existing engines into meaning columns
# and renders one board snapshot to Telegram. Inline column navigation is the next layer.
#
# Columns: сегодня (routines pending + reminders + focus) · активные (Twenty ACTIVE + next step) ·
# последняя миля (last-mile near-miss) · заморожено (Twenty FROZEN + frozen agents).
# Telegram via de-german (pcomp→api is DPI-cut), token via curl-config on stdin.
#
#   board.py show     — build + send the board to Telegram
#   board.py --dry    — print the board text, do not send
# stdlib only. Author: pluttan

import json
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"
SPAWN = SECRETARY / "spawn"
SECRETARYD = SECRETARY / "secretaryd"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()


def run_json(script, *args):
    try:
        out = subprocess.run(["python3.13", str(SPAWN / script), *args],
                             capture_output=True, text=True, timeout=45).stdout
        return json.loads(out)
    except Exception:
        return None


def _next_step(name):
    try:
        sys.path.insert(0, str(SPAWN))
        import moneypath
        return moneypath.next_step(moneypath.section(name))
    except Exception:
        return None


def collect():
    cols = {}

    # --- сегодня: фокус + рутины не сделано + напоминания ---
    prio = run_json("project_cmd.py", "prioritize")
    routines = run_json("routines.py", "pending")
    reminders = run_json("reminders.py", "list")
    today = []
    if (prio or {}).get("focus"):
        today.append(f"⚑ фокус: {prio['focus']}")
    for t in (routines or {}).get("pending", []):
        today.append(f"○ рутина ({t['slot']}): {t['title']}")
    for r in (reminders or {}).get("reminders", []):
        today.append(f"⏰ {r['due'][11:16]} {r['text']}")
    cols["сегодня"] = today

    # --- проекты по стадиям (Twenty) ---
    st = run_json("project_cmd.py", "status")
    tracks = (st or {}).get("tracks", [])
    active, frozen = [], []
    for t in tracks:
        stage = (t.get("stage") or "").upper()
        if stage == "ACTIVE":
            step = _next_step(t["name"])
            active.append(f"{t['name']}" + (f" → {step}" if step else ""))
        elif stage == "FROZEN":
            frozen.append(t["name"])
    cols["активные"] = active

    # --- последняя миля ---
    lm = run_json("lastmile.py")
    cols["последняя миля"] = [c["name"] for c in (lm or {}).get("candidates", [])]

    # --- заморожено: FROZEN-проекты + спящие агенты ---
    ag = run_json("agent_registry.py", "list")
    cols["заморожено"] = frozen + [f"агент: {a}" for a in (ag or {}).get("frozen", [])]

    return cols


def render(cols):
    blocks = []
    for name, cards in cols.items():
        body = "\n".join(f"   {c}" for c in cards) if cards else "   —"
        blocks.append(f"▌ {name.upper()}\n{body}")
    return "📋 доска секретаря\n\n" + "\n\n".join(blocks)


def _tg(method, **fields):
    try:
        token = (SECRETS / "telegram-bot-token").read_text().strip()
    except Exception:
        return None
    cfg = [f'url = "https://api.telegram.org/bot{token}/{method}"']
    for k, v in fields.items():
        cfg.append(f'data = "{k}={quote(str(v), safe="")}"')   # urlencode ourselves → safe one-line config
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "de-german",
                            "curl -s --max-time 20 -K -"],
                           input="\n".join(cfg) + "\n", capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        print(f"[board] tg {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def main():
    dry = "--dry" in sys.argv
    text = render(collect())
    if dry:
        print(text); return
    res = _tg("sendMessage", chat_id=CHAT_ID, text=text)
    ok = bool(res and res.get("ok"))
    print(json.dumps({"ok": ok, "sent": ok, "msg_id": (res or {}).get("result", {}).get("message_id")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
