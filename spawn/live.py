#!/usr/bin/env python3.13
# live.py — суточный ритм: утренний/вечерний дайджест (хвост R8, фаза S).
#
# НЕ новый детектор — тонкий ОРКЕСТРАТОР: собирает в одну сводку то, что уже
# считают другие движки (портфель, якоря режима, отложенные идеи, near-miss,
# дневник, план). Чтобы у Шики была одна команда на утро и вечер.
# (режим/якоря = M4 lifelog; дневник = M9 diary; план = planner — здесь только агрегация.)
#
#   live.py morning   — утренний расклад
#   live.py evening   — вечерний разбор
#
# stdlib only. Печатает JSON-сводку (Шики формулирует по-человечески). Author: pluttan

import json
import subprocess
import sys
from pathlib import Path

SPAWN = Path.home() / "secretary" / "spawn"


def run_json(script, *args):
    try:
        out = subprocess.run(["python3.13", str(SPAWN / script), *args],
                             capture_output=True, text=True, timeout=40).stdout
        return json.loads(out)
    except Exception:
        return None


def morning():
    portfolio = run_json("project_cmd.py", "status")
    prio = run_json("project_cmd.py", "prioritize")
    anchors = run_json("lifelog.py", "check")
    ideas = run_json("idea.py", "due")
    routines = run_json("routines.py", "pending", "morning")
    return {
        "kind": "morning",
        "focus": (prio or {}).get("focus"),
        "wip": (portfolio or {}).get("wip", {}).get("available"),
        "overdue_anchors": (anchors or {}).get("overdue_anchors", []),
        "due_ideas": (ideas or {}).get("due", []),
        "routine_pending": [t["title"] for t in (routines or {}).get("pending", [])],
        "note": "Шики: бодрое утро + расклад. Утренняя рутина (что не сделано), фокус дня, просроченные якоря режима (мягко), всплывшие идеи. Не вали всё сразу — 2-3 главного.",
    }


def evening():
    plan = run_json("planner.py", "--save")
    reward = run_json("rewardgate.py")
    lastmile = run_json("lastmile.py")
    routines = run_json("routines.py", "pending", "evening")
    rstats = run_json("routines.py", "stats", "7")
    return {
        "kind": "evening",
        "tomorrow_plan_saved": (plan or {}).get("saved"),
        "need_step": (plan or {}).get("need_step", []),
        "reward_earned": (reward or {}).get("reward_earned"),
        "lastmile_candidates": [c["name"] for c in (lastmile or {}).get("candidates", [])],
        "routine_pending": [t["title"] for t in (routines or {}).get("pending", [])],
        "streaks": (rstats or {}).get("streaks", {}),
        "note": "Шики: вечерний разбор. Не завис ли на последней миле (предъяви DoD/спроси), вечерняя рутина (что не сделано + streak), план на завтра (вытяни шаги), дневник (diary.py --save), доведения. По-доброму, без душнёжа на ночь.",
    }


def main():
    a = sys.argv[1:]
    if a and a[0] == "morning":
        print(json.dumps(morning(), ensure_ascii=False, indent=2))
    elif a and a[0] == "evening":
        print(json.dumps(evening(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "usage": "live.py morning | evening"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
