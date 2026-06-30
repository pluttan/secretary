#!/usr/bin/env python3.13
# board_labels.py — labels view for a card (ported from yougileTgBot task_detail_labels).
# Global label pool; tap a label to attach/detach it from the card. Callbacks routed via board.
#
#   b_lbtog:<cid>:<lid>    toggle label on card
#   b_lbdel:<lid>:<cid>    delete label globally (returns to card's label view)
#   b_addlabelc:<cid>      create a new label (pending text) + auto-attach to card
# Author: pluttan

import json
import board_db as bd


def _kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def _btn(text, data):
    return {"text": text, "callback_data": data}


def view_labels(c, cid):
    on = {l[0] for l in bd.card_labels(c, cid)}
    rows = []
    for lid, name, _color in bd.labels(c):
        mark = "◉" if lid in on else "○"
        rows.append([_btn(f"{mark} {name}", f"b_lbtog:{cid}:{lid}"),
                     _btn("🗑", f"b_lbdel:{lid}:{cid}")])
    if not bd.labels(c):
        rows.append([_btn("(меток ещё нет)", "b_noop")])
    rows.append([_btn("⊕ метка", f"b_addlabelc:{cid}"), _btn("‹ карточка", f"b_card:{cid}")])
    return "◎ метки карточки (тапни — прикрепить/снять):", _kb(rows)
