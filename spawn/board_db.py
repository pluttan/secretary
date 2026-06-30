#!/usr/bin/env python3.13
# board_db.py — data model + CRUD for the full kanban ported from yougileTgBot.
# Hierarchy: projects → boards → columns → cards. A card carries the rich fields of the
# prototype's task: done/archived, deadline (date+time), priority, description, recurring,
# plus subtasks (own table) and labels (global table + link table).
#
# stdlib only (sqlite3). Author: pluttan

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path.home() / "secretary" / "state" / "board.db"

PRIORITIES = ["—", "низкий", "средний", "высокий", "срочно"]   # index = priority int


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys=ON")
    _schema(c)
    _migrate(c)
    return c


def _schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, pos INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS boards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, title TEXT NOT NULL,
        pos INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, board_id INTEGER NOT NULL, title TEXT NOT NULL,
        color INTEGER DEFAULT 0, pos INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT, column_id INTEGER NOT NULL, title TEXT NOT NULL,
        done INTEGER DEFAULT 0, archived INTEGER DEFAULT 0, deadline TEXT, priority INTEGER DEFAULT 0,
        description TEXT DEFAULT '', recurring TEXT DEFAULT '', pos INTEGER DEFAULT 0,
        created TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS subtasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, title TEXT NOT NULL,
        done INTEGER DEFAULT 0, pos INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS labels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, color INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS card_labels (
        card_id INTEGER NOT NULL, label_id INTEGER NOT NULL, PRIMARY KEY (card_id, label_id));
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    c.commit()


def _migrate(c):
    """Bring a pre-existing v1 board.db (boards/columns/cards only) up to the rich schema."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(cards)")}
    for name, ddl in [("archived", "INTEGER DEFAULT 0"), ("priority", "INTEGER DEFAULT 0"),
                      ("description", "TEXT DEFAULT ''"), ("recurring", "TEXT DEFAULT ''")]:
        if name not in cols:
            c.execute(f"ALTER TABLE cards ADD COLUMN {name} {ddl}")
    bcols = {r[1] for r in c.execute("PRAGMA table_info(boards)")}
    if "project_id" not in bcols:
        c.execute("ALTER TABLE boards ADD COLUMN project_id INTEGER")
    ccols = {r[1] for r in c.execute("PRAGMA table_info(columns)")}
    if "color" not in ccols:
        c.execute("ALTER TABLE columns ADD COLUMN color INTEGER DEFAULT 0")
    c.commit()


def _rows(c, q, a=()):
    return c.execute(q, a).fetchall()


def _nextpos(c, table, where="", a=()):
    q = f"SELECT COALESCE(MAX(pos),-1)+1 FROM {table}" + (f" WHERE {where}" if where else "")
    return _rows(c, q, a)[0][0]


# --- projects ---
def projects(c):
    return _rows(c, "SELECT id, title FROM projects ORDER BY pos, id")


def add_project(c, title):
    return c.execute("INSERT INTO projects (title, pos) VALUES (?,?)",
                     (title, _nextpos(c, "projects"))).lastrowid


def del_project(c, pid):
    for b in _rows(c, "SELECT id FROM boards WHERE project_id=?", (pid,)):
        del_board(c, b[0])
    c.execute("DELETE FROM projects WHERE id=?", (pid,))


def project_title(c, pid):
    r = _rows(c, "SELECT title FROM projects WHERE id=?", (pid,))
    return r[0][0] if r else "?"


def rename_project(c, pid, title):
    c.execute("UPDATE projects SET title=? WHERE id=?", (title, pid))


# --- boards ---
def boards(c, pid=None):
    if pid is None:
        return _rows(c, "SELECT id, title FROM boards WHERE project_id IS NULL ORDER BY pos, id")
    return _rows(c, "SELECT id, title FROM boards WHERE project_id=? ORDER BY pos, id", (pid,))


def add_board(c, title, pid=None):
    return c.execute("INSERT INTO boards (title, project_id, pos) VALUES (?,?,?)",
                     (title, pid, _nextpos(c, "boards"))).lastrowid


def del_board(c, bid):
    for col in _rows(c, "SELECT id FROM columns WHERE board_id=?", (bid,)):
        del_column(c, col[0])
    c.execute("DELETE FROM boards WHERE id=?", (bid,))


def board_title(c, bid):
    r = _rows(c, "SELECT title FROM boards WHERE id=?", (bid,))
    return r[0][0] if r else "?"


def board_project(c, bid):
    r = _rows(c, "SELECT project_id FROM boards WHERE id=?", (bid,))
    return r[0][0] if r else None


def rename_board(c, bid, title):
    c.execute("UPDATE boards SET title=? WHERE id=?", (title, bid))


# --- columns ---
def columns(c, bid):
    return _rows(c, "SELECT id, title, color FROM columns WHERE board_id=? ORDER BY pos, id", (bid,))


def add_column(c, bid, title):
    return c.execute("INSERT INTO columns (board_id, title, pos) VALUES (?,?,?)",
                     (bid, title, _nextpos(c, "columns", "board_id=?", (bid,)))).lastrowid


def del_column(c, col):
    c.execute("DELETE FROM card_labels WHERE card_id IN (SELECT id FROM cards WHERE column_id=?)", (col,))
    c.execute("DELETE FROM subtasks WHERE card_id IN (SELECT id FROM cards WHERE column_id=?)", (col,))
    c.execute("DELETE FROM cards WHERE column_id=?", (col,))
    c.execute("DELETE FROM columns WHERE id=?", (col,))


def col_title(c, col):
    r = _rows(c, "SELECT title FROM columns WHERE id=?", (col,))
    return r[0][0] if r else "?"


def col_board(c, col):
    r = _rows(c, "SELECT board_id FROM columns WHERE id=?", (col,))
    return r[0][0] if r else None


def rename_column(c, col, title):
    c.execute("UPDATE columns SET title=? WHERE id=?", (title, col))


def set_column_color(c, col, color):
    c.execute("UPDATE columns SET color=? WHERE id=?", (int(color), col))


# --- cards ---
def cards(c, col, include_archived=False):
    q = "SELECT id, title, done, deadline, priority, archived FROM cards WHERE column_id=?"
    if not include_archived:
        q += " AND archived=0"
    q += " ORDER BY done, pos, id"
    return _rows(c, q, (col,))


def add_card(c, col, title):
    return c.execute("INSERT INTO cards (column_id, title, pos, created) VALUES (?,?,?,?)",
                     (col, title, _nextpos(c, "cards", "column_id=?", (col,)), _now_iso())).lastrowid


def card(c, cid):
    r = _rows(c, """SELECT id, title, done, archived, deadline, priority, description, recurring, column_id
                    FROM cards WHERE id=?""", (cid,))
    if not r:
        return None
    k = ["id", "title", "done", "archived", "deadline", "priority", "description", "recurring", "column_id"]
    return dict(zip(k, r[0]))


def toggle_card(c, cid):
    c.execute("UPDATE cards SET done=1-done WHERE id=?", (cid,))


def set_card_title(c, cid, title):
    c.execute("UPDATE cards SET title=? WHERE id=?", (title, cid))


def set_card_deadline(c, cid, deadline):
    c.execute("UPDATE cards SET deadline=? WHERE id=?", (deadline, cid))


def set_card_priority(c, cid, priority):
    c.execute("UPDATE cards SET priority=? WHERE id=?", (int(priority), cid))


def set_card_description(c, cid, desc):
    c.execute("UPDATE cards SET description=? WHERE id=?", (desc, cid))


def set_card_recurring(c, cid, rule):
    c.execute("UPDATE cards SET recurring=? WHERE id=?", (rule, cid))


def archive_card(c, cid, val=1):
    c.execute("UPDATE cards SET archived=? WHERE id=?", (int(val), cid))


def del_card(c, cid):
    c.execute("DELETE FROM card_labels WHERE card_id=?", (cid,))
    c.execute("DELETE FROM subtasks WHERE card_id=?", (cid,))
    c.execute("DELETE FROM cards WHERE id=?", (cid,))


def move_card(c, cid, to_col):
    c.execute("UPDATE cards SET column_id=?, pos=? WHERE id=?",
              (to_col, _nextpos(c, "cards", "column_id=?", (to_col,)), cid))


# --- subtasks ---
def subtasks(c, cid):
    return _rows(c, "SELECT id, title, done FROM subtasks WHERE card_id=? ORDER BY pos, id", (cid,))


def add_subtask(c, cid, title):
    return c.execute("INSERT INTO subtasks (card_id, title, pos) VALUES (?,?,?)",
                     (cid, title, _nextpos(c, "subtasks", "card_id=?", (cid,)))).lastrowid


def toggle_subtask(c, sid):
    c.execute("UPDATE subtasks SET done=1-done WHERE id=?", (sid,))


def del_subtask(c, sid):
    c.execute("DELETE FROM subtasks WHERE id=?", (sid,))


def subtask_card(c, sid):
    r = _rows(c, "SELECT card_id FROM subtasks WHERE id=?", (sid,))
    return r[0][0] if r else None


# --- labels ---
def labels(c):
    return _rows(c, "SELECT id, name, color FROM labels ORDER BY id")


def add_label(c, name, color=0):
    return c.execute("INSERT INTO labels (name, color) VALUES (?,?)", (name, int(color))).lastrowid


def del_label(c, lid):
    c.execute("DELETE FROM card_labels WHERE label_id=?", (lid,))
    c.execute("DELETE FROM labels WHERE id=?", (lid,))


def card_labels(c, cid):
    return _rows(c, """SELECT l.id, l.name, l.color FROM labels l
                       JOIN card_labels cl ON cl.label_id=l.id WHERE cl.card_id=? ORDER BY l.id""", (cid,))


def toggle_card_label(c, cid, lid):
    has = _rows(c, "SELECT 1 FROM card_labels WHERE card_id=? AND label_id=?", (cid, lid))
    if has:
        c.execute("DELETE FROM card_labels WHERE card_id=? AND label_id=?", (cid, lid))
    else:
        c.execute("INSERT INTO card_labels (card_id, label_id) VALUES (?,?)", (cid, lid))


# --- settings ---
def get_setting(c, key, default=None):
    r = _rows(c, "SELECT value FROM settings WHERE key=?", (key,))
    return r[0][0] if r else default


def set_setting(c, key, value):
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))


# --- cross-cutting queries (showall / search) ---
def all_cards(c, include_done=True, include_archived=False):
    """Every card across all boards, with column/board/project context. For showall + search."""
    q = """SELECT cd.id, cd.title, cd.done, cd.deadline, cd.priority, cd.archived,
                  col.title, b.title, p.title
           FROM cards cd
           JOIN columns col ON col.id=cd.column_id
           JOIN boards b ON b.id=col.board_id
           LEFT JOIN projects p ON p.id=b.project_id
           WHERE 1=1"""
    if not include_archived:
        q += " AND cd.archived=0"
    if not include_done:
        q += " AND cd.done=0"
    q += " ORDER BY (cd.deadline IS NULL), cd.deadline, cd.priority DESC"
    k = ["id", "title", "done", "deadline", "priority", "archived", "column", "board", "project"]
    return [dict(zip(k, r)) for r in _rows(c, q)]
