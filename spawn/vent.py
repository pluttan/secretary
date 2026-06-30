#!/usr/bin/env python3.13
# vent.py — режим «выговориться» (M10, фаза S).
#
# Когда у владельца острый узел (тон/обрыв/злость/усталость) — Шики переключается в
# РЕЖИМ ОТРАЖЕНИЯ и ГЛУШИТ все душнилки. Детект острого узла — дело Шики (тон диалога),
# не python. Этот движок делает механическую часть: глушит пинки M0, переиспользуя
# существующий флаг `## gate` секретарского STATE (off = вето, пинки молчат) — БЕЗ
# правки кода secretaryd (M0 священ). Маркер `~/secretary/state/.vent` хранит, что режим активен.
#
#   vent.py on    — включить (gate→off, душнилки молчат)
#   vent.py off   — выключить (gate→on, вернуть как было)
#   vent.py status
#
# stdlib only. Author: pluttan

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE = Path.home() / "secretary" / "state" / "STATE.md"
VENT = Path.home() / "secretary" / "state" / ".vent"


def set_gate(value):
    """Установить значение секции `## gate` (on|off), сохранив остальное."""
    lines = STATE.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == "## gate"), -1)
    if start < 0:
        raise RuntimeError("в STATE нет секции '## gate'")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")), len(lines))
    head = lines[start + 1:end]
    comments = [l for l in head if l.strip().startswith("<!--")]
    new = lines[:start + 1] + comments + [value, ""] + lines[end:]
    STATE.write_text("\n".join(new) + "\n", encoding="utf-8")


def cur_gate():
    sec = None
    for raw in STATE.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            sec = s[3:].strip().lower(); continue
        if sec == "gate" and s and not s.startswith("<!--"):
            return s.lower()
    return "on"


def main():
    a = sys.argv[1:]
    ts = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    if a and a[0] == "on":
        set_gate("off")
        VENT.write_text(json.dumps({"active": True, "since": ts}), encoding="utf-8")
        print(json.dumps({"ok": True, "vent": "on", "gate": "off",
                          "note": "душнилки заглушены. Шики: РЕЖИМ ОТРАЖЕНИЯ — слушать/отражать, НЕ давить, НЕ советовать пока не попросит. Выйти: vent.py off когда отпустит."}, ensure_ascii=False))
    elif a and a[0] == "off":
        set_gate("on")
        if VENT.exists():
            VENT.unlink()
        print(json.dumps({"ok": True, "vent": "off", "gate": "on", "note": "режим снят, пинки M0 вернулись"}, ensure_ascii=False))
    elif a and a[0] == "status":
        active = VENT.exists()
        print(json.dumps({"ok": True, "vent_active": active, "gate": cur_gate(),
                          "since": (json.loads(VENT.read_text()).get("since") if active else None)}, ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "usage": "vent.py on | off | status"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
