#!/usr/bin/env python3.13
# routines.py — habit tracker for the secretary (mechanism ported from the yougileTgBot
# prototype: morning/evening routine tasks + daily completion + streaks; the telegram/Gemini
# layers are NOT ported — the openclaw persona drives this via commands, live asks/marks).
#
# Self-onboarding: the owner adds routines by command (never hard-coded). Slots: morning /
# evening / day. Today's completion lives in a log; streak = consecutive days where every
# active task of a slot was done.
#
#   routines.py add <morning|evening|day> <title...>
#   routines.py list [slot]                 — active routines
#   routines.py done <title-or-id> [--date YYYY-MM-DD]
#   routines.py undone <title-or-id>
#   routines.py drop <title-or-id>          — deactivate
#   routines.py pending [slot]              — active & not done today (JSON)
#   routines.py stats [days]                — per-day completion + streak per slot (JSON)
# stdlib only (sqlite3). Author: pluttan

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB = Path.home() / "secretary" / "state" / "routines.db"
SLOTS = ("morning", "evening", "day")


def _today():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")  # MSK date


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL DEFAULT 'day',
        title TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS log (
        date TEXT NOT NULL, task_id INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (date, task_id))""")
    return c


def _resolve(c, ident):
    """Find an active task id by numeric id or case-insensitive title substring."""
    if ident.isdigit():
        r = c.execute("SELECT id FROM tasks WHERE id=? AND active=1", (int(ident),)).fetchone()
        return r[0] if r else None
    r = c.execute("SELECT id FROM tasks WHERE active=1 AND lower(title) LIKE ? ORDER BY id LIMIT 1",
                  (f"%{ident.lower()}%",)).fetchone()
    return r[0] if r else None


def add(slot, title):
    slot = slot if slot in SLOTS else "day"
    with _conn() as c:
        cur = c.execute("INSERT INTO tasks (slot, title, active, created) VALUES (?,?,1,?)",
                        (slot, title.strip(), _today()))
        return {"ok": True, "id": cur.lastrowid, "slot": slot, "title": title.strip()}


def listing(slot=None):
    with _conn() as c:
        q = "SELECT id, slot, title FROM tasks WHERE active=1"
        args = ()
        if slot in SLOTS:
            q += " AND slot=?"; args = (slot,)
        rows = c.execute(q + " ORDER BY slot, id", args).fetchall()
    return {"ok": True, "tasks": [{"id": r[0], "slot": r[1], "title": r[2]} for r in rows]}


def mark(ident, done=1, date=None):
    date = date or _today()
    with _conn() as c:
        tid = _resolve(c, ident)
        if tid is None:
            return {"ok": False, "error": "no_such_routine", "hint": "routines.py list — посмотреть"}
        c.execute("INSERT OR REPLACE INTO log (date, task_id, done) VALUES (?,?,?)", (date, tid, done))
        t = c.execute("SELECT title, slot FROM tasks WHERE id=?", (tid,)).fetchone()
    return {"ok": True, "id": tid, "title": t[0], "slot": t[1], "done": bool(done), "date": date}


def drop(ident):
    with _conn() as c:
        tid = _resolve(c, ident)
        if tid is None:
            return {"ok": False, "error": "no_such_routine"}
        c.execute("UPDATE tasks SET active=0 WHERE id=?", (tid,))
        t = c.execute("SELECT title FROM tasks WHERE id=?", (tid,)).fetchone()
    return {"ok": True, "id": tid, "title": t[0], "dropped": True}


def pending(slot=None, date=None):
    date = date or _today()
    with _conn() as c:
        q = "SELECT id, slot, title FROM tasks WHERE active=1"
        args = []
        if slot in SLOTS:
            q += " AND slot=?"; args.append(slot)
        rows = c.execute(q, args).fetchall()
        done_ids = {r[0] for r in c.execute("SELECT task_id FROM log WHERE date=? AND done=1", (date,)).fetchall()}
    pend = [{"id": r[0], "slot": r[1], "title": r[2]} for r in rows if r[0] not in done_ids]
    return {"ok": True, "date": date, "pending": pend}


def _streak(c, slot):
    """Consecutive days (ending today or yesterday) where ALL active slot-tasks were done."""
    ids = [r[0] for r in c.execute("SELECT id FROM tasks WHERE active=1 AND slot=?", (slot,)).fetchall()]
    if not ids:
        return 0
    streak, day = 0, datetime.strptime(_today(), "%Y-%m-%d")
    for i in range(0, 366):
        d = (day - timedelta(days=i)).strftime("%Y-%m-%d")
        done = {r[0] for r in c.execute("SELECT task_id FROM log WHERE date=? AND done=1", (d,)).fetchall()}
        if all(t in done for t in ids):
            streak += 1
        elif i == 0:
            continue          # today not finished yet — don't break the streak
        else:
            break
    return streak


def stats(days=7):
    with _conn() as c:
        out = {}
        base = datetime.strptime(_today(), "%Y-%m-%d")
        for i in range(days - 1, -1, -1):
            d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
            total = c.execute("SELECT COUNT(*) FROM tasks WHERE active=1").fetchone()[0]
            done = c.execute("SELECT COUNT(*) FROM log WHERE date=? AND done=1", (d,)).fetchone()[0]
            out[d] = {"done": done, "total": total}
        streaks = {s: _streak(c, s) for s in SLOTS if c.execute(
            "SELECT 1 FROM tasks WHERE active=1 AND slot=? LIMIT 1", (s,)).fetchone()}
    return {"ok": True, "by_day": out, "streaks": streaks}


def main():
    a = sys.argv[1:]
    if not a:
        print(json.dumps({"error": "usage: add|list|done|undone|drop|pending|stats"})); return
    cmd, rest = a[0], a[1:]
    pos = [x for x in rest if not x.startswith("--")]
    date = next((rest[i + 1] for i, x in enumerate(rest) if x == "--date" and i + 1 < len(rest)), None)
    if cmd == "add" and len(pos) >= 2:
        print(json.dumps(add(pos[0], " ".join(pos[1:])), ensure_ascii=False)); return
    if cmd == "list":
        print(json.dumps(listing(pos[0] if pos else None), ensure_ascii=False, indent=2)); return
    if cmd == "done" and pos:
        print(json.dumps(mark(pos[0], 1, date), ensure_ascii=False)); return
    if cmd == "undone" and pos:
        print(json.dumps(mark(pos[0], 0, date), ensure_ascii=False)); return
    if cmd == "drop" and pos:
        print(json.dumps(drop(pos[0]), ensure_ascii=False)); return
    if cmd == "pending":
        print(json.dumps(pending(pos[0] if pos else None, date), ensure_ascii=False, indent=2)); return
    if cmd == "stats":
        print(json.dumps(stats(int(pos[0]) if pos and pos[0].isdigit() else 7), ensure_ascii=False, indent=2)); return
    print(json.dumps({"error": "usage: add|list|done|undone|drop|pending|stats"}))


if __name__ == "__main__":
    main()
