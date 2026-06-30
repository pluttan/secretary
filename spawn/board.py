#!/usr/bin/env python3.13
# board.py — full kanban board for the secretary, rewritten under telegram inline / openclaw
# (porting the yougileTgBot board UX, not its pyTelegramBotAPI menu framework). One message,
# navigated in depth via inline buttons + editMessageText: BOARDS → COLUMNS → CARDS → CARD,
# with CRUD at each level. Callbacks are routed in by the shared reminders poll (one getUpdates).
#
# Own SQLite (boards/columns/cards). One special board "Секретарь" is the OVERLAY: its columns
# are read-only, aggregated live from the engines (today/active/last-mile/frozen). User boards
# are fully editable.
#
#   board.py show               — open the boards list in telegram
#   board.py --dry              — print a text snapshot of all boards
# Telegram via de-german (pcomp→api is DPI-cut), token via curl-config on stdin.
# stdlib only (sqlite3). Author: pluttan

import json
import sqlite3
import subprocess
import sys
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"
SPAWN = SECRETARY / "spawn"
DB = SECRETARY / "state" / "board.db"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

OVERLAY = "Секретарь"     # special read-only board aggregated from engines


# ==========================
# ===  Storage           ===
# ==========================

def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS boards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, pos INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, board_id INTEGER NOT NULL, title TEXT NOT NULL,
        pos INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, column_id INTEGER NOT NULL, title TEXT NOT NULL,
        done INTEGER DEFAULT 0, deadline TEXT, pos INTEGER DEFAULT 0,
        created TEXT NOT NULL DEFAULT '')""")
    return c


def _rows(c, q, args=()):
    return c.execute(q, args).fetchall()


# --- boards ---
def boards(c):
    return _rows(c, "SELECT id, title FROM boards ORDER BY pos, id")


def add_board(c, title):
    p = _rows(c, "SELECT COALESCE(MAX(pos),-1)+1 FROM boards")[0][0]
    return c.execute("INSERT INTO boards (title, pos) VALUES (?,?)", (title, p)).lastrowid


def del_board(c, bid):
    cols = [r[0] for r in _rows(c, "SELECT id FROM columns WHERE board_id=?", (bid,))]
    for col in cols:
        c.execute("DELETE FROM cards WHERE column_id=?", (col,))
    c.execute("DELETE FROM columns WHERE board_id=?", (bid,))
    c.execute("DELETE FROM boards WHERE id=?", (bid,))


# --- columns ---
def columns(c, bid):
    return _rows(c, "SELECT id, title FROM columns WHERE board_id=? ORDER BY pos, id", (bid,))


def add_column(c, bid, title):
    p = _rows(c, "SELECT COALESCE(MAX(pos),-1)+1 FROM columns WHERE board_id=?", (bid,))[0][0]
    return c.execute("INSERT INTO columns (board_id, title, pos) VALUES (?,?,?)", (bid, title, p)).lastrowid


def del_column(c, col):
    c.execute("DELETE FROM cards WHERE column_id=?", (col,))
    c.execute("DELETE FROM columns WHERE id=?", (col,))


# --- cards ---
def cards(c, col):
    return _rows(c, "SELECT id, title, done, deadline FROM cards WHERE column_id=? ORDER BY done, pos, id", (col,))


def add_card(c, col, title):
    p = _rows(c, "SELECT COALESCE(MAX(pos),-1)+1 FROM cards WHERE column_id=?", (col,))[0][0]
    return c.execute("INSERT INTO cards (column_id, title, pos, created) VALUES (?,?,?,?)",
                     (col, title, p, datetime.now(timezone.utc).isoformat())).lastrowid


def toggle_card(c, card):
    c.execute("UPDATE cards SET done=1-done WHERE id=?", (card,))


def del_card(c, card):
    c.execute("DELETE FROM cards WHERE id=?", (card,))


def move_card(c, card, board_to_col):
    c.execute("UPDATE cards SET column_id=?, done=0 WHERE id=?", (board_to_col, card))


def card_one(c, card):
    r = _rows(c, "SELECT id, title, done, deadline, column_id FROM cards WHERE id=?", (card,))
    return r[0] if r else None


def col_board(c, col):
    r = _rows(c, "SELECT board_id FROM columns WHERE id=?", (col,))
    return r[0][0] if r else None


def board_title(c, bid):
    r = _rows(c, "SELECT title FROM boards WHERE id=?", (bid,))
    return r[0][0] if r else "?"


def col_title(c, col):
    r = _rows(c, "SELECT title FROM columns WHERE id=?", (col,))
    return r[0][0] if r else "?"


# ==========================
# ===  Overlay (engines) ===
# ==========================

def _run_json(script, *args):
    try:
        out = subprocess.run(["python3.13", str(SPAWN / script), *args],
                             capture_output=True, text=True, timeout=45).stdout
        return json.loads(out)
    except Exception:
        return None


def overlay_columns():
    """The read-only Секретарь board: columns aggregated live from the engines."""
    prio = _run_json("project_cmd.py", "prioritize")
    routines = _run_json("routines.py", "pending")
    rem = _run_json("reminders.py", "list")
    st = _run_json("project_cmd.py", "status")
    lm = _run_json("lastmile.py")
    ag = _run_json("agent_registry.py", "list")
    tracks = (st or {}).get("tracks", [])
    active = [t["name"] for t in tracks if (t.get("stage") or "").upper() == "ACTIVE"]
    frozen = [t["name"] for t in tracks if (t.get("stage") or "").upper() == "FROZEN"]
    today = []
    if (prio or {}).get("focus"):
        today.append(f"⚑ фокус: {prio['focus']}")
    today += [f"○ рутина ({t['slot']}): {t['title']}" for t in (routines or {}).get("pending", [])]
    today += [f"⏰ {r['due'][11:16]} {r['text']}" for r in (rem or {}).get("reminders", [])]
    return [
        ("сегодня", today),
        ("активные", active),
        ("последняя миля", [c["name"] for c in (lm or {}).get("candidates", [])]),
        ("заморожено", frozen + [f"агент: {a}" for a in (ag or {}).get("frozen", [])]),
    ]


# ==========================
# ===  Telegram          ===
# ==========================

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


def _kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _btn(text, data):
    return {"text": text, "callback_data": data}


# ==========================
# ===  Views (render + keyboard per level)
# ==========================

def view_boards(c):
    text = "📋 ДОСКИ\n\nвыбери доску:"
    rows = [[_btn(f"▦ {OVERLAY}", "b_ov")]]
    for bid, title in boards(c):
        rows.append([_btn(f"▦ {title}", f"b_brd:{bid}")])
    rows.append([_btn("⊕ доска", "b_addbrd")])
    return text, _kb(rows)


def view_overlay():
    cols = overlay_columns()
    lines = [f"▦ {OVERLAY} (живой срез движков, read-only)\n"]
    for name, cards_ in cols:
        body = "\n".join(f"   {x}" for x in cards_) if cards_ else "   —"
        lines.append(f"▌ {name.upper()} ({len(cards_)})\n{body}")
    rows = [[_btn("↻ обновить", "b_ov"), _btn("‹ доски", "b_boards")]]
    return "\n\n".join(lines), _kb(rows)


def view_board(c, bid):
    title = board_title(c, bid)
    cols = columns(c, bid)
    lines = [f"▦ {title}\n"]
    for col_id, ctitle in cols:
        cs = cards(c, col_id)
        done = sum(1 for x in cs if x[2])
        lines.append(f"▌ {ctitle} — {len(cs)} карт ({done}✓)")
    if not cols:
        lines.append("   (колонок нет)")
    rows = [[_btn(f"› {ctitle}", f"b_col:{col_id}")] for col_id, ctitle in cols]
    rows.append([_btn("⊕ колонка", f"b_addcol:{bid}"), _btn("🗑 доску", f"b_delbrd:{bid}")])
    rows.append([_btn("‹ доски", "b_boards")])
    return "\n".join(lines), _kb(rows)


def view_column(c, col):
    bid = col_board(c, col)
    lines = [f"▌ {col_title(c, col)}  ·  {board_title(c, bid)}\n"]
    cs = cards(c, col)
    if not cs:
        lines.append("   (пусто)")
    rows = []
    for cid, ctitle, done, _dl in cs:
        mark = "✓" if done else "○"
        lines.append(f"{mark} {ctitle}")
        rows.append([_btn(f"{mark} {ctitle[:30]}", f"b_card:{cid}")])
    rows.append([_btn("⊕ карточка", f"b_addcard:{col}"), _btn("🗑 колонку", f"b_delcol:{col}")])
    rows.append([_btn("‹ доска", f"b_brd:{bid}")])
    return "\n".join(lines), _kb(rows)


def view_card(c, card):
    r = card_one(c, card)
    if not r:
        return "карточка удалена", _kb([[_btn("‹ доски", "b_boards")]])
    cid, title, done, deadline, col = r
    bid = col_board(c, col)
    text = f"🗂 {title}\n\nстатус: {'✓ готово' if done else '○ в работе'}\nколонка: {col_title(c, col)}"
    rows = [
        [_btn("○ снять" if done else "✓ готово", f"b_toggle:{card}")],
        [_btn("→ переместить", f"b_movemenu:{card}")],
        [_btn("🗑 удалить", f"b_delcard:{card}"), _btn("‹ колонка", f"b_col:{col}")],
    ]
    return text, _kb(rows)


def view_movemenu(c, card):
    r = card_one(c, card)
    if not r:
        return "карточка удалена", _kb([[_btn("‹ доски", "b_boards")]])
    col = r[4]
    bid = col_board(c, col)
    rows = [[_btn(f"→ {ctitle}", f"b_moveto:{card}:{cid}")]
            for cid, ctitle in columns(c, bid) if cid != col]
    rows.append([_btn("‹ карточка", f"b_card:{card}")])
    return "куда переместить карточку?", _kb(rows)


# ==========================
# ===  Callback router    ===
# ==========================

def handle_callback(data, cq):
    """Routed in by the shared reminders poll. Edits the message to the navigated view.
    Some actions need a follow-up text reply (add) → handled via a pending-input marker."""
    msg = cq.get("message", {})
    chat = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")

    def edit(text, kb):
        _tg("editMessageText", chat_id=chat, message_id=mid, text=text, reply_markup=kb)

    with _conn() as c:
        act, _, arg = data.partition(":")
        a2 = arg.split(":") if arg else []

        if act == "b_boards":
            edit(*view_boards(c))
        elif act == "b_ov":
            edit(*view_overlay())
        elif act == "b_brd":
            edit(*view_board(c, int(a2[0])))
        elif act == "b_col":
            edit(*view_column(c, int(a2[0])))
        elif act == "b_card":
            edit(*view_card(c, int(a2[0])))
        elif act == "b_toggle":
            toggle_card(c, int(a2[0])); edit(*view_card(c, int(a2[0])))
        elif act == "b_delcard":
            card = card_one(c, int(a2[0])); col = card[4] if card else None
            del_card(c, int(a2[0]))
            edit(*(view_column(c, col) if col else view_boards(c)))
        elif act == "b_movemenu":
            edit(*view_movemenu(c, int(a2[0])))
        elif act == "b_moveto":
            move_card(c, int(a2[0]), int(a2[1])); edit(*view_card(c, int(a2[0])))
        elif act == "b_delcol":
            bid = col_board(c, int(a2[0])); del_column(c, int(a2[0])); edit(*view_board(c, bid))
        elif act == "b_delbrd":
            del_board(c, int(a2[0])); edit(*view_boards(c))
        elif act in ("b_addbrd", "b_addcol", "b_addcard"):
            # mark pending text input; the persona/bot collects the title, then calls `board.py add ...`
            _set_pending(act, arg)
            kinds = {"b_addbrd": "доски", "b_addcol": "колонки", "b_addcard": "карточки"}
            edit(f"пришли название {kinds[act]} одним сообщением (или /cancel).",
                 _kb([[_btn("‹ назад", "b_boards")]]))
        else:
            return False
    return True


PENDING = SECRETARY / "state" / ".board-pending"


def _set_pending(act, arg):
    PENDING.write_text(f"{act}:{arg}", encoding="utf-8")


def apply_pending(title):
    """Called when the user sends the title for a pending add (persona wires this)."""
    if not PENDING.exists():
        return {"ok": False, "error": "no_pending"}
    spec = PENDING.read_text().strip()
    PENDING.unlink(missing_ok=True)
    act, _, arg = spec.partition(":")
    with _conn() as c:
        if act == "b_addbrd":
            bid = add_board(c, title); return {"ok": True, "board": bid}
        if act == "b_addcol":
            cid = add_column(c, int(arg), title); return {"ok": True, "column": cid}
        if act == "b_addcard":
            cid = add_card(c, int(arg), title); return {"ok": True, "card": cid}
    return {"ok": False, "error": "bad_pending"}


# ==========================
# ===  CLI               ===
# ==========================

def show():
    with _conn() as c:
        text, kb = view_boards(c)
    return _tg("sendMessage", chat_id=CHAT_ID, text=text, reply_markup=kb)


def render_all():
    with _conn() as c:
        out = [f"▦ {OVERLAY} (живой срез движков)"]
        for name, cs in overlay_columns():
            out.append(f"  ▌ {name}: " + (", ".join(cs) if cs else "—"))
        for bid, title in boards(c):
            out.append(f"\n▦ {title}")
            for col_id, ctitle in columns(c, bid):
                cs = cards(c, col_id)
                out.append(f"  ▌ {ctitle}: " + (", ".join(f"{'✓' if x[2] else '○'}{x[1]}" for x in cs) if cs else "—"))
    return "\n".join(out)


def main():
    a = sys.argv[1:]
    if "--dry" in a:
        print(render_all()); return
    if a and a[0] == "addtitle" and len(a) >= 2:           # persona: apply pending add with title
        print(json.dumps(apply_pending(" ".join(a[1:])), ensure_ascii=False)); return
    res = show()
    ok = bool(res and res.get("ok"))
    print(json.dumps({"ok": ok, "sent": ok, "msg_id": (res or {}).get("result", {}).get("message_id")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
