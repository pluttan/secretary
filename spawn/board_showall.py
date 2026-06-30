#!/usr/bin/env python3.13
# board_showall.py — cross-board task list with deadline filters + full-text search
# (ported from yougileTgBot showall). Every open card across all boards, filtered by how
# soon its deadline falls (3д/нед/мес/год/все), each row jumping straight into the card.
# Callbacks routed via board.
#
#   b_show:<filt>   show all with deadline filter (3d|week|month|year|all)
#   b_search        → pending query; board.apply_pending sends results
# Author: pluttan

import json
from datetime import date, timedelta
import board_db as bd

FILTERS = [("3d", "3д", 3), ("week", "нед", 7), ("month", "мес", 30),
           ("year", "год", 365), ("all", "все", None)]
_DAYS = {f[0]: f[2] for f in FILTERS}


def _kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _btn(text, data):
    return {"text": text, "callback_data": data}


def _within(deadline, days):
    if days is None:
        return True
    if not deadline:
        return False
    try:
        return date.fromisoformat(deadline[:10]) <= date.today() + timedelta(days=days)
    except ValueError:
        return False


def view_showall(c, filt="week"):
    days = _DAYS.get(filt, 7)
    cards = [cd for cd in bd.all_cards(c, include_done=False) if _within(cd["deadline"], days)]
    lines = [f"▤ все задачи · фильтр: {filt}\n"]
    rows = []
    for cd in cards[:40]:
        dl = f" ·{cd['deadline'][:10]}" if cd["deadline"] else ""
        ctx = cd["board"] or cd["project"] or "—"
        lines.append(f"{bd.PRIORITIES[cd['priority']][:1] or '○'} {cd['title']}{dl}  «{ctx}»")
        rows.append([_btn(f"{cd['title'][:30]}{dl}", f"b_card:{cd['id']}")])
    if not cards:
        lines.append("   (нет задач под фильтр)")
    rows.append([_btn(("● " if f[0] == filt else "") + f[1], f"b_show:{f[0]}") for f in FILTERS])
    rows.append([_btn("🔍 поиск", "b_search"), _btn("‹ доски", "b_root")])
    return "\n".join(lines), _kb(rows)


def view_search(c, query):
    ql = query.lower().strip()
    cards = [cd for cd in bd.all_cards(c, include_archived=True) if ql in cd["title"].lower()]
    lines = [f"🔍 поиск: «{query}» — найдено {len(cards)}\n"]
    rows = []
    for cd in cards[:40]:
        mark = "✓" if cd["done"] else "○"
        ctx = cd["board"] or cd["project"] or "—"
        lines.append(f"{mark} {cd['title']}  «{ctx}»")
        rows.append([_btn(f"{mark} {cd['title'][:32]}", f"b_card:{cd['id']}")])
    if not cards:
        lines.append("   (ничего не нашлось)")
    rows.append([_btn("‹ доски", "b_root")])
    return "\n".join(lines), _kb(rows)
