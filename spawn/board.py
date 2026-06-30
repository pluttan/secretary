#!/usr/bin/env python3.13
# board.py — the kanban OVERLAY over all the secretary's mechanisms (yougileTgBot-style board,
# aggregating not storing). Pulls cards from the existing engines into meaning columns and shows
# ONE column at a time with an inline column-switcher row; tapping a column edits the message to
# that column (kanban paging). Column-switch callbacks are routed in by the shared reminders poll
# (one getUpdates), so there is no second telegram poller.
#
# Columns: сегодня (focus + routines pending + reminders) · активные (Twenty ACTIVE + next step) ·
# последняя миля (last-mile near-miss) · заморожено (Twenty FROZEN + frozen agents).
# Telegram via de-german (pcomp→api is DPI-cut), token via curl-config on stdin.
#
#   board.py show     — build + send the board (first column + switcher)
#   board.py --dry    — print all columns as text, do not send
# stdlib only. Author: pluttan

import json
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"
SPAWN = SECRETARY / "spawn"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

COL_ORDER = ["сегодня", "активные", "последняя миля", "заморожено"]
COL_SHORT = {"сегодня": "сегодня", "активные": "активные", "последняя миля": "финиш", "заморожено": "заморож"}


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

    lm = run_json("lastmile.py")
    cols["последняя миля"] = [c["name"] for c in (lm or {}).get("candidates", [])]

    ag = run_json("agent_registry.py", "list")
    cols["заморожено"] = frozen + [f"агент: {a}" for a in (ag or {}).get("frozen", [])]
    return cols


def render_column(col, cols):
    cards = cols.get(col, [])
    body = "\n".join(f"   {c}" for c in cards) if cards else "   —"
    return f"📋 доска · {col.upper()} ({len(cards)})\n\n{body}"


def render_all(cols):
    blocks = [f"▌ {n.upper()}\n" + ("\n".join(f"   {c}" for c in cards) if cards else "   —")
              for n, cards in cols.items()]
    return "📋 доска секретаря\n\n" + "\n\n".join(blocks)


def keyboard(current, cols):
    row = [{"text": ("● " if c == current else "") + COL_SHORT.get(c, c),
            "callback_data": f"board_col:{c}"} for c in COL_ORDER if c in cols]
    return json.dumps({"inline_keyboard": [row]}, ensure_ascii=False)


def _tg(method, **fields):
    try:
        token = (SECRETS / "telegram-bot-token").read_text().strip()
    except Exception:
        return None
    cfg = [f'url = "https://api.telegram.org/bot{token}/{method}"']
    for k, v in fields.items():
        cfg.append(f'data = "{k}={quote(str(v), safe="")}"')
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "de-german",
                            "curl -s --max-time 20 -K -"],
                           input="\n".join(cfg) + "\n", capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        print(f"[board] tg {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def show():
    cols = collect()
    first = next((c for c in COL_ORDER if c in cols), COL_ORDER[0])
    return _tg("sendMessage", chat_id=CHAT_ID, text=render_column(first, cols),
               reply_markup=keyboard(first, cols))


def handle_callback(data, cq):
    """Called by the shared reminders poll for board_col:<name> presses → edit message to that column."""
    _, _, col = data.partition(":")
    cols = collect()
    if col not in cols:
        return False
    msg = cq.get("message", {})
    _tg("editMessageText", chat_id=msg.get("chat", {}).get("id"), message_id=msg.get("message_id"),
        text=render_column(col, cols), reply_markup=keyboard(col, cols))
    return True


def main():
    if "--dry" in sys.argv:
        print(render_all(collect())); return
    res = show()
    ok = bool(res and res.get("ok"))
    print(json.dumps({"ok": ok, "sent": ok, "msg_id": (res or {}).get("result", {}).get("message_id")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
