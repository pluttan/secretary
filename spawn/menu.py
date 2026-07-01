#!/usr/bin/env python3.13
# menu.py — single inline hub for the secretary. One telegram message with buttons to every engine;
# tap → editMessageText swaps to that section + [‹ меню]. Callbacks (m_*) are routed in by the
# shared reminders poll. "Доска" hands off to board's own navigation (b_root); "Год" sends the
# year picture as a photo. No-egress-safe: read-only summaries, redaction on the way out.
# BMP-only glyphs (mosh-safe). stdlib only. Author: pluttan

import json
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact
import secret

SECRETARY = Path.home() / "secretary"
SPAWN = SECRETARY / "spawn"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))

SECTIONS = [
    ("m_board", "▦ Доска"), ("m_comms", "✉ Переписки"),
    ("m_day", "◷ День"), ("m_hours", "◔ Часы"),
    ("m_routines", "○ Рутины"), ("m_rem", "◉ Напоминания"),
    ("m_year", "▤ Год"), ("m_invest", "₽ Инвест"),
    ("m_infra", "⚙ Инфра"),
]


# ---------- telegram ----------
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
        print(f"[menu] tg {method}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _btn(text, data):
    return {"text": text, "callback_data": data}


def _run_json(script, *args):
    try:
        out = subprocess.run(["python3.13", str(SPAWN / script), *args],
                             capture_output=True, text=True, timeout=60).stdout
        return json.loads(out)
    except Exception:
        return None


def _back():
    return _kb([[_btn("‹ меню", "m_menu")]])


# ---------- root ----------
def view_menu():
    rows, row = [], []
    for cid, label in SECTIONS:
        row.append(_btn(label, cid))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return "секретарь — меню\nвыбери раздел:", _kb(rows)


# ---------- sections (concise read-only summaries) ----------
def sec_comms():
    d = _run_json("comms.py", "digest") or {}
    lines = ["✉ ПЕРЕПИСКИ (рабочий тг)\n",
             f"долги по ответам: {d.get('reply_debt', '?')}",
             f"обещания мои/их:  {d.get('promises_mine', '?')}/{d.get('promises_theirs', '?')}",
             f"дедлайны (личных): {d.get('deadlines', '?')} ({d.get('deadlines_personal', '?')})",
             f"остывшие связи:   {d.get('ghosts', '?')}"]
    for x in (d.get("top_debt") or [])[:3]:
        lines.append(f"  {x['age_days']}д — {x['who'][:24]}")
    return "\n".join(lines)


def sec_day():
    d = _run_json("anchors.py", "today") or {}
    items = d.get("today", [])
    lines = ["◷ ЯКОРЯ СЕГОДНЯ\n"] + ([f"  {x['when']} {x['what']}" for x in items[:12]] or ["  —"])
    return "\n".join(lines)


def sec_hours():
    try:
        import golden_hours
        return golden_hours.render()
    except Exception as e:
        return f"◔ часы: ошибка ({type(e).__name__})"


def sec_routines():
    d = _run_json("routines.py", "pending") or {}
    p = d.get("pending", [])
    lines = ["○ РУТИНЫ (не сделано)\n"] + ([f"  ({t['slot']}) {t['title']}" for t in p] or ["  всё сделано"])
    return "\n".join(lines)


def sec_rem():
    d = _run_json("reminders.py", "list") or {}
    r = d.get("reminders", [])
    lines = ["◉ НАПОМИНАНИЯ\n"] + ([f"  {x['due'][5:16]} {x['text'][:32]}" for x in r[:10]] or ["  нет активных"])
    return "\n".join(lines)


def sec_invest():
    d = _run_json("invest.py", "balance") or {}
    return (f"₽ ИНВЕСТ\n\nбаланс: {d.get('balance', '?')}\nдолг: {d.get('debt', '?')}\n"
            f"net: {d.get('net', '?')}\nобновлён: {d.get('last_update', '—')}")


def sec_infra():
    d = _run_json("guard.py", "status") or {}
    disk = "  ".join(f"{x['path']} {x['pct']}%" for x in d.get("disk", []))
    b = d.get("backup", {})
    m = d.get("mac", {})
    return (f"⚙ ИНФРА\n\nдиск: {disk}\n"
            f"бэкап: {b.get('age_days', '?')} дн\n"
            f"мак: {'жив' if m.get('alive') else 'НЕДОСТУПЕН'}")


_SECTION_FN = {"m_comms": sec_comms, "m_day": sec_day, "m_hours": sec_hours,
               "m_routines": sec_routines, "m_rem": sec_rem, "m_invest": sec_invest,
               "m_infra": sec_infra}


# ---------- callback router ----------
def handle_callback(data, cq):
    msg = cq.get("message", {})
    chat = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")

    def edit(text, kb):
        _tg("editMessageText", chat_id=chat, message_id=mid, text=text, reply_markup=kb)

    if data == "m_menu":
        t, kb = view_menu(); edit(t, kb); return True
    if data == "m_board":
        import board
        board.handle_callback("b_root", cq); return True       # hand off to board's own nav
    if data == "m_year":
        edit("▤ собираю год-картинку…", _back())
        _run_json("reports_img.py", "year")                    # sends the photo separately
        edit("▤ год-картинка отправлена выше.", _back()); return True
    fn = _SECTION_FN.get(data)
    if fn:
        edit(fn(), _back()); return True
    return False


# ---------- CLI ----------
def show():
    t, kb = view_menu()
    return _tg("sendMessage", chat_id=CHAT_ID, text=t, reply_markup=kb)


def main():
    a = sys.argv[1:]
    if a and a[0] == "--dry":
        for cid, label in SECTIONS:
            fn = _SECTION_FN.get(cid)
            print(f"--- {label} ---"); print(fn() if fn else "(навигация)"); print()
        return
    res = show()
    ok = bool(res and res.get("ok"))
    print(json.dumps({"ok": ok, "msg_id": (res or {}).get("result", {}).get("message_id")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
