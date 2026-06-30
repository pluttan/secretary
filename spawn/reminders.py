#!/usr/bin/env python3.13
# reminders.py — scheduled reminders with inline ack buttons (mechanism from the yougileTgBot
# prototype, rewritten clean). The owner sets a reminder; at due time the secretary sends it to
# Telegram with inline buttons [✓ готово | +1ч | завтра], then polls callbacks and acts.
#
# Telegram bot API is reached via `ssh de-german` (pcomp's direct route to api.telegram.org is
# DPI-cut), token passed through a curl config on stdin (never in argv). Closes the §11 inline-
# buttons / getUpdates-poll tech debt.
#
#   reminders.py add "<ISO due | HH:MM | +Nm | +Nh>" <text...>   — the persona parses human time → due
#   reminders.py list                                            — active reminders (JSON)
#   reminders.py cancel <id>
#   reminders.py tick                                            — send due + poll acks (driven by timer)
# stdlib only (sqlite3). Author: pluttan

import json
import sqlite3
import subprocess
import sys
from urllib.parse import quote
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"
DB = SECRETARY / "state" / "reminders.db"
MSK = ZoneInfo("Europe/Moscow")

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()


def _now():
    return datetime.now(MSK)


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, due TEXT NOT NULL, text TEXT NOT NULL,
        repeat TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        msg_id INTEGER, created TEXT NOT NULL)""")
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return c


def parse_due(s):
    """Accept ISO, 'HH:MM' (today/tomorrow), '+Nm', '+Nh'. Returns ISO MSK or None."""
    s = s.strip()
    now = _now()
    try:
        if s.startswith("+") and s[-1] in "mh" and s[1:-1].isdigit():
            n = int(s[1:-1])
            return (now + timedelta(minutes=n if s[-1] == "m" else n * 60)).isoformat()
        if len(s) <= 5 and ":" in s:                       # HH:MM
            h, m = (int(x) for x in s.split(":"))
            d = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if d <= now:
                d += timedelta(days=1)                     # passed today → tomorrow
            return d.isoformat()
        return datetime.fromisoformat(s).replace(tzinfo=MSK).isoformat() if "T" in s \
            else datetime.fromisoformat(s).replace(tzinfo=MSK).isoformat()
    except Exception:
        return None


def add(due_raw, text):
    due = parse_due(due_raw)
    if not due:
        return {"ok": False, "error": "bad_time", "hint": "due: ISO, HH:MM, +30m, +2h"}
    with _conn() as c:
        cur = c.execute("INSERT INTO reminders (due, text, created) VALUES (?,?,?)",
                        (due, text.strip(), _now().isoformat()))
        return {"ok": True, "id": cur.lastrowid, "due": due, "text": text.strip()}


def listing():
    with _conn() as c:
        rows = c.execute("SELECT id, due, text, status FROM reminders WHERE status IN ('pending','sent') ORDER BY due").fetchall()
    return {"ok": True, "reminders": [{"id": r[0], "due": r[1], "text": r[2], "status": r[3]} for r in rows]}


def cancel(rid):
    with _conn() as c:
        c.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (int(rid),))
    return {"ok": True, "id": int(rid), "cancelled": True}


# ==========================
# ===  Telegram via de-german
# ==========================

def _tg(method, **fields):
    """Call a telegram bot API method via de-german. Token via curl-config on stdin (not argv).
    Returns parsed JSON, or None on failure."""
    try:
        token = (SECRETS / "telegram-bot-token").read_text().strip()
    except Exception as e:
        print(f"[rem] no token: {type(e).__name__}", file=sys.stderr)
        return None
    cfg = [f'url = "https://api.telegram.org/bot{token}/{method}"']
    for k, v in fields.items():
        cfg.append(f'data = "{k}={quote(str(v), safe="")}"')   # urlencode ourselves → safe one-line config
    payload = "\n".join(cfg) + "\n"
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "de-german",
             "curl -s --max-time 20 -K -"],
            input=payload, capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        print(f"[rem] tg {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _keyboard(rid):
    return json.dumps({"inline_keyboard": [[
        {"text": "✓ готово", "callback_data": f"rem_ack:{rid}"},
        {"text": "+1ч", "callback_data": f"rem_1h:{rid}"},
        {"text": "завтра", "callback_data": f"rem_1d:{rid}"},
    ]]}, ensure_ascii=False)


def send_due():
    sent = []
    with _conn() as c:
        now_iso = _now().isoformat()
        due = c.execute("SELECT id, text FROM reminders WHERE status='pending' AND due<=?", (now_iso,)).fetchall()
        for rid, text in due:
            res = _tg("sendMessage", chat_id=CHAT_ID, text=f"⏰ {text}", reply_markup=_keyboard(rid))
            if res and res.get("ok"):
                mid = res["result"]["message_id"]
                c.execute("UPDATE reminders SET status='sent', msg_id=? WHERE id=?", (mid, rid))
                sent.append(rid)
    return sent


def poll():
    """getUpdates (offset) → handle callback presses on our reminders."""
    handled = []
    with _conn() as c:
        off = c.execute("SELECT value FROM meta WHERE key='tg_offset'").fetchone()
        offset = int(off[0]) if off else 0
        upd = _tg("getUpdates", offset=offset, timeout=0, allowed_updates='["callback_query"]')
        if not upd or not upd.get("ok"):
            return handled
        for u in upd["result"]:
            offset = max(offset, u["update_id"] + 1)
            cq = u.get("callback_query")
            if not cq:
                continue
            data = cq.get("data", "")
            if not data.startswith("rem_"):
                continue
            action, _, rid = data.partition(":")
            try:
                rid = int(rid)
            except ValueError:
                continue
            if action == "rem_ack":
                c.execute("UPDATE reminders SET status='acked' WHERE id=?", (rid,))
                note = "✓ отметил"
            elif action == "rem_1h":
                c.execute("UPDATE reminders SET status='pending', due=? WHERE id=?",
                          ((_now() + timedelta(hours=1)).isoformat(), rid))
                note = "напомню через час"
            elif action == "rem_1d":
                c.execute("UPDATE reminders SET status='pending', due=? WHERE id=?",
                          ((_now() + timedelta(days=1)).isoformat(), rid))
                note = "перенёс на завтра"
            else:
                continue
            _tg("answerCallbackQuery", callback_query_id=cq["id"], text=note)
            handled.append({"id": rid, "action": action})
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('tg_offset', ?)", (str(offset),))
    return handled


def main():
    a = sys.argv[1:]
    if not a:
        print(json.dumps({"error": "usage: add|list|cancel|tick"})); return
    cmd, rest = a[0], a[1:]
    if cmd == "add" and len(rest) >= 2:
        print(json.dumps(add(rest[0], " ".join(rest[1:])), ensure_ascii=False)); return
    if cmd == "list":
        print(json.dumps(listing(), ensure_ascii=False, indent=2)); return
    if cmd == "cancel" and rest:
        print(json.dumps(cancel(rest[0]), ensure_ascii=False)); return
    if cmd == "tick":
        s = send_due()
        h = poll()
        print(json.dumps({"ok": True, "sent": s, "handled": h}, ensure_ascii=False)); return
    print(json.dumps({"error": "usage: add|list|cancel|tick"}))


if __name__ == "__main__":
    main()
