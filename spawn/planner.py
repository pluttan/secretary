#!/usr/bin/env python3.13
# planner.py — вечерний план на завтра (хвост R8, фаза S).
#
# Вечером собирает черновик плана на завтра по АКТИВНЫМ проектам: для каждого —
# ближайший шаг (из money-path next, M7). Нет шага → пометить «определить». Нет
# активных идей → подсказать freeze (по плану: нет идей → заморозка). Шики
# показывает черновик и спрашивает «что на завтра?» — план владельца, не зашитый.
#
# stdlib only. Печатает черновик + JSON-сигнал. Author: pluttan

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
SPAWN_DIR = Path.home() / "secretary" / "spawn"
STATE_DIR = Path.home() / "secretary" / "state"


def active_projects():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as tw
    return [t.get("name") for t in tw.list_tracks(first=200) if (t.get("stage") or "").upper() == "ACTIVE"]


def next_step_for(name):
    try:
        sys.path.insert(0, str(SPAWN_DIR))
        import moneypath
        return moneypath.next_step(moneypath.section(name))
    except Exception:
        return None


def main():
    save = "--save" in sys.argv
    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=3) + timedelta(days=1)).strftime("%Y-%m-%d")
    act = active_projects()
    lines = [f"# План на завтра — {tomorrow}", ""]
    need_step = []
    if not act:
        lines.append("- активных проектов нет. Что берём в работу завтра? (или ничего — это ок, freeze).")
    for p in act:
        ns = next_step_for(p)
        if ns:
            lines.append(f"- {p}: {ns}")
        else:
            lines.append(f"- {p}: ШАГ НЕ ОПРЕДЕЛЁН — какой ближайший конкретный шаг?")
            need_step.append(p)
    lines += ["", "## правки владельца", "<!-- сюда твой реальный план; Шики не зашивает -->", ""]
    draft = "\n".join(lines)
    if save:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        f = STATE_DIR / f"plan-{tomorrow}.md"
        f.write_text(draft, encoding="utf-8")
        print(json.dumps({"ok": True, "saved": str(f), "active": act, "need_step": need_step}, ensure_ascii=False))
    else:
        print(draft)
        print("\n<!-- need_step: " + json.dumps(need_step, ensure_ascii=False) + " active_none: " + str(not act) + " -->")


if __name__ == "__main__":
    main()
