#!/usr/bin/env python3.13
# board.py — full kanban for the secretary under telegram inline (yougileTgBot UX, rewritten).
# Navigation in depth via one message + editMessageText:
#   ROOT (projects + loose boards + Секретарь-overlay) → project → board → column → card → detail.
# CRUD at every level; the card detail carries priority / subtasks / description / move / archive
# (deadline picker → board_cal, labels → board_labels are wired as they land). Callbacks routed in
# by the shared reminders poll (prefix b_). Data model + storage live in board_db.
#
#   board.py show        — open root in telegram
#   board.py --dry       — text snapshot
#   board.py addtitle …  — apply a pending text input (persona wires this)
# stdlib only. Author: pluttan

import json
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_db as bd

SECRETARY = Path.home() / "secretary"
SPAWN = SECRETARY / "spawn"
PENDING = SECRETARY / "state" / ".board-pending"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

OVERLAY = "Секретарь"
INBOX_BOARD = "Входящие"
PRIO_MARK = {0: "", 1: "▽", 2: "◇", 3: "△", 4: "‼"}
RECUR = ["", "ежедневно", "еженедельно", "ежемесячно", "ежегодно"]


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


def _card_line(row):
    """row = (id, title, done, deadline, priority, archived)"""
    _id, title, done, deadline, prio, _arch = row
    mark = "✓" if done else "○"
    pm = PRIO_MARK.get(prio or 0, "")
    dl = f" ·{deadline[:10]}" if deadline else ""
    return f"{mark}{pm} {title}{dl}"


# ==========================
# ===  Views             ===
# ==========================

def view_root(c):
    text = "📋 ДОСКИ\n\nпроекты, доски и живой срез секретаря:"
    rows = [[_btn(f"▦ {OVERLAY}", "b_ov")]]
    for pid, title in bd.projects(c):
        nb = len(bd.boards(c, pid))
        rows.append([_btn(f"▣ {title} ({nb})", f"b_proj:{pid}")])
    for bid, title in bd.boards(c, None):                     # boards not in any project
        rows.append([_btn(f"▦ {title}", f"b_brd:{bid}")])
    rows.append([_btn("▤ все задачи", "b_show:week"), _btn("🔍 поиск", "b_search"),
                 _btn("⚙", "b_settings")])
    rows.append([_btn("⊕ проект", "b_addproj"), _btn("⊕ доска", "b_addbrd:0")])
    return text, _kb(rows)


def view_overlay():
    cols = overlay_columns()
    lines = [f"▦ {OVERLAY} — живой срез движков (read-only)\n"]
    for name, items in cols:
        body = "\n".join(f"   {x}" for x in items) if items else "   —"
        lines.append(f"▌ {name.upper()} ({len(items)})\n{body}")
    return "\n\n".join(lines), _kb([[_btn("↻ обновить", "b_ov"), _btn("‹ доски", "b_root")]])


def view_project(c, pid):
    lines = [f"▣ {bd.project_title(c, pid)}\n"]
    bs = bd.boards(c, pid)
    for bid, title in bs:
        lines.append(f"▦ {title} — {len(bd.columns(c, bid))} кол.")
    if not bs:
        lines.append("   (досок нет)")
    rows = [[_btn(f"› {title}", f"b_brd:{bid}")] for bid, title in bs]
    rows.append([_btn("⊕ доска", f"b_addbrd:{pid}"), _btn("✎", f"b_renproj:{pid}"),
                 _btn("🗑", f"b_delproj:{pid}")])
    rows.append([_btn("‹ доски", "b_root")])
    return "\n".join(lines), _kb(rows)


def view_board(c, bid):
    lines = [f"▦ {bd.board_title(c, bid)}\n"]
    cs = bd.columns(c, bid)
    for col_id, ctitle, _color in cs:
        cards_ = bd.cards(c, col_id)
        done = sum(1 for x in cards_ if x[2])
        lines.append(f"▌ {ctitle} — {len(cards_)} карт ({done}✓)")
    if not cs:
        lines.append("   (колонок нет)")
    rows = [[_btn(f"› {ctitle}", f"b_col:{col_id}")] for col_id, ctitle, _c in cs]
    rows.append([_btn("⊕ колонка", f"b_addcol:{bid}"), _btn("✎", f"b_renbrd:{bid}"),
                 _btn("🗑", f"b_delbrd:{bid}")])
    pid = bd.board_project(c, bid)
    rows.append([_btn("‹ назад", f"b_proj:{pid}" if pid else "b_root")])
    return "\n".join(lines), _kb(rows)


def view_column(c, col):
    bid = bd.col_board(c, col)
    lines = [f"▌ {bd.col_title(c, col)}  ·  {bd.board_title(c, bid)}\n"]
    cards_ = bd.cards(c, col)
    if bd.get_setting(c, "hide_completed", "false") == "true":
        cards_ = [x for x in cards_ if not x[2]]
    if not cards_:
        lines.append("   (пусто)")
    rows = []
    for row in cards_:
        lines.append(_card_line(row))
        rows.append([_btn(_card_line(row)[:36], f"b_card:{row[0]}")])
    rows.append([_btn("⊕ карточка", f"b_addcard:{col}"), _btn("✎", f"b_rencol:{col}"),
                 _btn("🗑", f"b_delcol:{col}")])
    rows.append([_btn("‹ доска", f"b_brd:{bid}")])
    return "\n".join(lines), _kb(rows)


def view_card(c, cid):
    cd = bd.card(c, cid)
    if not cd:
        return "карточка удалена", _kb([[_btn("‹ доски", "b_root")]])
    subs = bd.subtasks(c, cid)
    sdone = sum(1 for s in subs if s[2])
    labs = bd.card_labels(c, cid)
    lines = [f"🗂 {cd['title']}\n"]
    lines.append(f"статус:    {'✓ готово' if cd['done'] else '○ в работе'}")
    lines.append(f"приоритет: {bd.PRIORITIES[cd['priority']]}")
    lines.append(f"дедлайн:   {cd['deadline'] or '—'}")
    if cd['recurring']:
        lines.append(f"повтор:    {cd['recurring']}")
    if labs:
        lines.append("метки:     " + ", ".join(l[1] for l in labs))
    if subs:
        lines.append(f"подзадачи: {sdone}/{len(subs)}")
    if cd['description']:
        lines.append(f"\n{cd['description']}")
    rows = [
        [_btn("○ снять" if cd['done'] else "✓ готово", f"b_toggle:{cid}"),
         _btn("⚑ приоритет", f"b_prio:{cid}")],
        [_btn("⏱ дедлайн", f"b_deadline:{cid}"), _btn("◎ метки", f"b_labels:{cid}")],
        [_btn(f"☑ подзадачи ({sdone}/{len(subs)})", f"b_subs:{cid}"),
         _btn("✎ описание", f"b_desc:{cid}")],
        [_btn("↻ повтор", f"b_recur:{cid}"), _btn("✎ имя", f"b_editcard:{cid}")],
        [_btn("→ перенести", f"b_movemenu:{cid}"), _btn("⌂ в архив", f"b_arch:{cid}")],
        [_btn("🗑 удалить", f"b_delcard:{cid}"), _btn("‹ колонка", f"b_col:{cd['column_id']}")],
    ]
    return "\n".join(lines), _kb(rows)


def view_recurring(c, cid):
    rows = [[_btn(name or "без повтора", f"b_setrecur:{cid}:{i}")] for i, name in enumerate(RECUR)]
    rows.append([_btn("‹ карточка", f"b_card:{cid}")])
    return "↻ повтор карточки:", _kb(rows)


def view_priority(c, cid):
    rows = [[_btn(f"{PRIO_MARK.get(i,'')} {name}".strip(), f"b_setprio:{cid}:{i}")]
            for i, name in enumerate(bd.PRIORITIES)]
    rows.append([_btn("‹ карточка", f"b_card:{cid}")])
    return "приоритет карточки:", _kb(rows)


def view_subtasks(c, cid):
    cd = bd.card(c, cid)
    if not cd:
        return "карточка удалена", _kb([[_btn("‹ доски", "b_root")]])
    subs = bd.subtasks(c, cid)
    lines = [f"☑ подзадачи · {cd['title']}\n"]
    rows = []
    for sid, title, done in subs:
        mark = "✓" if done else "○"
        lines.append(f"{mark} {title}")
        rows.append([_btn(f"{mark} {title[:28]}", f"b_subtog:{sid}"), _btn("🗑", f"b_subdel:{sid}")])
    if not subs:
        lines.append("   (нет)")
    rows.append([_btn("⊕ подзадача", f"b_addsub:{cid}"), _btn("‹ карточка", f"b_card:{cid}")])
    return "\n".join(lines), _kb(rows)


def view_move(c, cid):
    cd = bd.card(c, cid)
    if not cd:
        return "карточка удалена", _kb([[_btn("‹ доски", "b_root")]])
    bid = bd.col_board(c, cd['column_id'])
    rows = [[_btn(f"→ {ctitle}", f"b_moveto:{cid}:{col_id}")]
            for col_id, ctitle, _c in bd.columns(c, bid) if col_id != cd['column_id']]
    rows.append([_btn("‹ карточка", f"b_card:{cid}")])
    return "куда перенести карточку?", _kb(rows)


def view_settings(c):
    hide = bd.get_setting(c, "hide_completed", "false") == "true"
    rows = [
        [_btn(f"{'☑' if hide else '☐'} скрывать выполненные", "b_togset:hide_completed")],
        [_btn("‹ доски", "b_root")],
    ]
    return "⚙ настройки доски:", _kb(rows)


def quick_add(title):
    """Fast capture: parse macros, drop a card into the loose 'Входящие' board (auto-created)."""
    import board_macro
    p = board_macro.parse(title)
    with bd.conn() as c:
        inbox = next((bid for bid, t in bd.boards(c, None) if t == INBOX_BOARD), None)
        if not inbox:
            inbox = bd.add_board(c, INBOX_BOARD, None)
        cols = bd.columns(c, inbox)
        col = cols[0][0] if cols else bd.add_column(c, inbox, "новое")
        card = bd.add_card(c, col, p["title"] or title.strip())
        if p["priority"]:
            bd.set_card_priority(c, card, p["priority"])
        if p["deadline"]:
            bd.set_card_deadline(c, card, p["deadline"])
        if p["description"]:
            bd.set_card_description(c, card, p["description"])
        for lname in p["labels"]:
            lid = next((l[0] for l in bd.labels(c) if l[1] == lname), None) or bd.add_label(c, lname)
            bd.toggle_card_label(c, card, lid)
        c.commit()
        return {"ok": True, "card": card, "board": inbox, "parsed": p}


# ==========================
# ===  Pending text input ===
# ==========================

_KIND = {"b_addproj": "проекта", "b_addbrd": "доски", "b_addcol": "колонки",
         "b_addcard": "карточки", "b_addsub": "подзадачи", "b_addlabel": "метки",
         "b_editcard": "новое имя карточки", "b_desc": "описание",
         "b_renproj": "новое имя проекта", "b_renbrd": "новое имя доски", "b_rencol": "новое имя колонки"}


def _set_pending(act, arg):
    PENDING.write_text(f"{act}:{arg}", encoding="utf-8")


def apply_pending(text):
    if not PENDING.exists():
        return {"ok": False, "error": "no_pending"}
    act, _, arg = PENDING.read_text().strip().partition(":")
    PENDING.unlink(missing_ok=True)
    text = text.strip()
    with bd.conn() as c:
        if act == "b_search":                                  # search → send results as new message
            import board_showall
            tk = board_showall.view_search(c, text)
            _tg("sendMessage", chat_id=CHAT_ID, text=tk[0], reply_markup=tk[1])
            return {"ok": True, "search": text}
        if act == "b_addproj":
            return {"ok": True, "project": bd.add_project(c, text)}
        if act == "b_addbrd":
            pid = int(arg) or None
            return {"ok": True, "board": bd.add_board(c, text, pid)}
        if act == "b_addcol":
            return {"ok": True, "column": bd.add_column(c, int(arg), text)}
        if act == "b_addcard":
            return {"ok": True, "card": bd.add_card(c, int(arg), text)}
        if act == "b_addsub":
            return {"ok": True, "subtask": bd.add_subtask(c, int(arg), text)}
        if act == "b_addlabel":
            lid = bd.add_label(c, text)
            if arg:                                            # created from a card → auto-attach
                bd.toggle_card_label(c, int(arg), lid)
            return {"ok": True, "label": lid}
        if act == "b_editcard":
            bd.set_card_title(c, int(arg), text); return {"ok": True}
        if act == "b_desc":
            bd.set_card_description(c, int(arg), text); return {"ok": True}
        if act == "b_renproj":
            bd.rename_project(c, int(arg), text); return {"ok": True}
        if act == "b_renbrd":
            bd.rename_board(c, int(arg), text); return {"ok": True}
        if act == "b_rencol":
            bd.rename_column(c, int(arg), text); return {"ok": True}
    return {"ok": False, "error": "bad_pending"}


# ==========================
# ===  Callback router    ===
# ==========================

def handle_callback(data, cq):
    msg = cq.get("message", {})
    chat = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")

    def edit(tk):
        text, kb = tk
        _tg("editMessageText", chat_id=chat, message_id=mid, text=text, reply_markup=kb)

    act, _, arg = data.partition(":")
    a = arg.split(":") if arg else []
    with bd.conn() as c:
        # --- navigation ---
        if act == "b_root":
            edit(view_root(c))
        elif act == "b_ov":
            edit(view_overlay())
        elif act == "b_proj":
            edit(view_project(c, int(a[0])))
        elif act == "b_brd":
            edit(view_board(c, int(a[0])))
        elif act == "b_col":
            edit(view_column(c, int(a[0])))
        elif act == "b_card":
            edit(view_card(c, int(a[0])))
        elif act == "b_prio":
            edit(view_priority(c, int(a[0])))
        elif act == "b_recur":
            edit(view_recurring(c, int(a[0])))
        elif act == "b_setrecur":
            bd.set_card_recurring(c, int(a[0]), RECUR[int(a[1])]); edit(view_card(c, int(a[0])))
        elif act == "b_subs":
            edit(view_subtasks(c, int(a[0])))
        elif act == "b_movemenu":
            edit(view_move(c, int(a[0])))
        elif act == "b_show":
            import board_showall
            edit(board_showall.view_showall(c, a[0]))
        elif act == "b_search":
            _set_pending("b_search", "")
            edit(("пришли слово для поиска по всем задачам.", _kb([[_btn("‹ доски", "b_root")]])))
        elif act == "b_settings":
            edit(view_settings(c))
        elif act == "b_togset":
            cur = bd.get_setting(c, a[0], "false")
            bd.set_setting(c, a[0], "false" if cur == "true" else "true"); edit(view_settings(c))
        # --- card actions ---
        elif act == "b_toggle":
            bd.toggle_card(c, int(a[0])); edit(view_card(c, int(a[0])))
        elif act == "b_setprio":
            bd.set_card_priority(c, int(a[0]), int(a[1])); edit(view_card(c, int(a[0])))
        elif act == "b_arch":
            cd = bd.card(c, int(a[0])); col = cd['column_id'] if cd else None
            bd.archive_card(c, int(a[0])); edit(view_column(c, col) if col else view_root(c))
        elif act == "b_delcard":
            cd = bd.card(c, int(a[0])); col = cd['column_id'] if cd else None
            bd.del_card(c, int(a[0])); edit(view_column(c, col) if col else view_root(c))
        elif act == "b_moveto":
            bd.move_card(c, int(a[0]), int(a[1])); edit(view_card(c, int(a[0])))
        # --- subtasks ---
        elif act == "b_subtog":
            cid = bd.subtask_card(c, int(a[0])); bd.toggle_subtask(c, int(a[0])); edit(view_subtasks(c, cid))
        elif act == "b_subdel":
            cid = bd.subtask_card(c, int(a[0])); bd.del_subtask(c, int(a[0])); edit(view_subtasks(c, cid))
        # --- deletes higher up ---
        elif act == "b_delcol":
            bid = bd.col_board(c, int(a[0])); bd.del_column(c, int(a[0])); edit(view_board(c, bid))
        elif act == "b_delbrd":
            pid = bd.board_project(c, int(a[0])); bd.del_board(c, int(a[0]))
            edit(view_project(c, pid) if pid else view_root(c))
        elif act == "b_delproj":
            bd.del_project(c, int(a[0])); edit(view_root(c))
        # --- deadline (board_cal date+time picker) ---
        elif act == "b_deadline":
            import board_cal
            edit(board_cal.view_picker(int(a[0])))
        elif act == "b_calnav":
            import board_cal
            y, m = board_cal.nav_month(a[1], a[2], a[3])
            edit(board_cal.view_picker(int(a[0]), y, m))
        elif act == "b_calday":
            import board_cal
            edit(board_cal.view_time(int(a[0]), a[1], a[2], a[3]))
        elif act == "b_caltime":
            import board_cal
            bd.set_card_deadline(c, int(a[0]), board_cal.compose_deadline(a[1], a[2]))
            edit(view_card(c, int(a[0])))
        elif act == "b_calclr":
            bd.set_card_deadline(c, int(a[0]), None)
            edit(view_card(c, int(a[0])))
        elif act == "b_noop":
            pass
        # --- labels (board_labels) ---
        elif act == "b_labels":
            import board_labels
            edit(board_labels.view_labels(c, int(a[0])))
        elif act == "b_lbtog":
            import board_labels
            bd.toggle_card_label(c, int(a[0]), int(a[1])); edit(board_labels.view_labels(c, int(a[0])))
        elif act == "b_lbdel":
            import board_labels
            bd.del_label(c, int(a[0])); edit(board_labels.view_labels(c, int(a[1])))
        elif act == "b_addlabelc":
            _set_pending("b_addlabel", a[0])
            edit((f"пришли название метки одним сообщением.", _kb([[_btn("‹ отмена", f"b_labels:{a[0]}")]])))
        # --- pending text inputs ---
        elif act in _KIND:
            _set_pending(act, arg)
            edit((f"пришли {_KIND[act]} одним сообщением.", _kb([[_btn("‹ отмена", "b_root")]])))
        else:
            return False
    return True


# ==========================
# ===  CLI               ===
# ==========================

def show():
    with bd.conn() as c:
        tk = view_root(c)
    return _tg("sendMessage", chat_id=CHAT_ID, text=tk[0], reply_markup=tk[1])


def render_all():
    with bd.conn() as c:
        out = [f"▦ {OVERLAY} (живой срез движков)"]
        for name, items in overlay_columns():
            out.append(f"  ▌ {name}: " + (", ".join(items) if items else "—"))
        for pid, ptitle in bd.projects(c):
            out.append(f"\n▣ {ptitle}")
            for bid, btitle in bd.boards(c, pid):
                out.append(f"  ▦ {btitle}")
                for col_id, ctitle, _c in bd.columns(c, bid):
                    cs = bd.cards(c, col_id)
                    out.append(f"    ▌ {ctitle}: " + (", ".join(_card_line(x) for x in cs) if cs else "—"))
        for bid, btitle in bd.boards(c, None):
            out.append(f"\n▦ {btitle} (без проекта)")
            for col_id, ctitle, _c in bd.columns(c, bid):
                cs = bd.cards(c, col_id)
                out.append(f"    ▌ {ctitle}: " + (", ".join(_card_line(x) for x in cs) if cs else "—"))
    return "\n".join(out)


def main():
    a = sys.argv[1:]
    if "--dry" in a:
        print(render_all()); return
    if a and a[0] == "addtitle" and len(a) >= 2:
        print(json.dumps(apply_pending(" ".join(a[1:])), ensure_ascii=False)); return
    if a and a[0] == "quick" and len(a) >= 2:
        print(json.dumps(quick_add(" ".join(a[1:])), ensure_ascii=False)); return
    res = show()
    ok = bool(res and res.get("ok"))
    print(json.dumps({"ok": ok, "sent": ok, "msg_id": (res or {}).get("result", {}).get("message_id")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
