#!/usr/bin/env python3.13
# invest.py — manual portfolio-balance ledger (ported from yougileTgBot invest). Not a market
# tracker — just a hand-kept journal: current balance, a history of changes (date/amount/reason/
# balance-after), and an optional debt figure. Fits the finance aspect (M6). stdlib only (sqlite3).
#
#   invest.py balance                      — current balance + debt (JSON)
#   invest.py add <amount> <reason...>     — apply a delta (+/-) to balance, log it
#   invest.py set <amount> [reason...]     — set balance outright (logs correction)
#   invest.py debt <amount>                — set the debt figure
#   invest.py history [N]                  — last N changes (default 30)
# Author: pluttan

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path.home() / "secretary" / "state" / "invest.db"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS invest_settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS invest_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, amount REAL NOT NULL,
        reason TEXT DEFAULT '', balance_after REAL NOT NULL, created TEXT NOT NULL);
    """)
    return c


def _get(c, key, default=None):
    r = c.execute("SELECT value FROM invest_settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _set(c, key, value):
    c.execute("INSERT OR REPLACE INTO invest_settings (key, value) VALUES (?,?)", (key, str(value)))


def balance():
    with _conn() as c:
        bal = float(_get(c, "balance", 0) or 0)
        debt = float(_get(c, "debt", 0) or 0)
        return {"ok": True, "balance": bal, "debt": debt, "net": bal - debt,
                "last_update": _get(c, "last_update", "")}


def add(amount, reason):
    try:
        amt = float(str(amount).replace(",", ".").replace("+", ""))
    except ValueError:
        return {"ok": False, "error": "bad_amount"}
    with _conn() as c:
        bal = float(_get(c, "balance", 0) or 0) + amt
        _set(c, "balance", bal)
        _set(c, "last_update", _today())
        c.execute("INSERT INTO invest_history (date, amount, reason, balance_after, created) VALUES (?,?,?,?,?)",
                  (_today(), amt, reason.strip(), bal, _now()))
        return {"ok": True, "amount": amt, "balance": bal, "reason": reason.strip()}


def set_balance(amount, reason="коррекция"):
    try:
        target = float(str(amount).replace(",", "."))
    except ValueError:
        return {"ok": False, "error": "bad_amount"}
    with _conn() as c:
        bal = float(_get(c, "balance", 0) or 0)
        delta = target - bal
        _set(c, "balance", target)
        _set(c, "last_update", _today())
        c.execute("INSERT INTO invest_history (date, amount, reason, balance_after, created) VALUES (?,?,?,?,?)",
                  (_today(), delta, reason.strip(), target, _now()))
        return {"ok": True, "balance": target, "delta": delta}


def set_debt(amount):
    try:
        d = float(str(amount).replace(",", "."))
    except ValueError:
        return {"ok": False, "error": "bad_amount"}
    with _conn() as c:
        _set(c, "debt", d)
        return {"ok": True, "debt": d}


def history(limit=30):
    with _conn() as c:
        rows = c.execute("""SELECT date, amount, reason, balance_after FROM invest_history
                            ORDER BY id DESC LIMIT ?""", (int(limit),)).fetchall()
    return {"ok": True, "history": [
        {"date": r[0], "amount": r[1], "reason": r[2], "balance_after": r[3]} for r in rows]}


def main():
    a = sys.argv[1:]
    if not a or a[0] == "balance":
        print(json.dumps(balance(), ensure_ascii=False)); return
    if a[0] == "add" and len(a) >= 2:
        print(json.dumps(add(a[1], " ".join(a[2:])), ensure_ascii=False)); return
    if a[0] == "set" and len(a) >= 2:
        print(json.dumps(set_balance(a[1], " ".join(a[2:]) or "коррекция"), ensure_ascii=False)); return
    if a[0] == "debt" and len(a) >= 2:
        print(json.dumps(set_debt(a[1]), ensure_ascii=False)); return
    if a[0] == "history":
        print(json.dumps(history(a[1] if len(a) > 1 else 30), ensure_ascii=False, indent=2)); return
    print(json.dumps({"error": "usage: balance|add <amt> <reason>|set <amt>|debt <amt>|history [N]"}))


if __name__ == "__main__":
    main()
