#!/usr/bin/env python3.13
# lastmile.py — детектор near-miss «завис на последней миле» (M6, фаза S).
#
# Лекарство от недоведения: ловит проекты, которые ACTIVE, но активность по ним
# падает (anchor=last-touch стареет) ИЛИ они выпали из текущих приоритетов
# (`## now`), а до SHIPPED так и не дошли. Это КАНДИДАТЫ — не приговор: Шики потом
# СПРАШИВАЕТ владельца (его DoD по проекту, «что осталось ровно», ритуал сдачи).
#
# Что зашито: НИЧЕГО про конкретные цели. Порог «застоя» (STALE_DAYS), DoD по
# проекту и ритуал сдачи — КОНТЕНТ владельца, собирается диалогом (самоонбординг M6):
#   - порог: ~/secretary/spawn/lastmile.conf (одна строка — число дней; деф. 3)
#   - DoD по проекту: ~/secretary/state/dod.md  (секции `## <project>` — заводит диалог)
#
# stdlib only. Печатает JSON. Author: pluttan

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
STATE_MD = Path.home() / "secretary" / "state" / "STATE.md"
DOD_MD = Path.home() / "secretary" / "state" / "dod.md"
STALE_CONF = Path.home() / "secretary" / "spawn" / "lastmile.conf"
DEFAULT_STALE_DAYS = 3


def stale_days():
    try:
        if STALE_CONF.exists():
            v = float(STALE_CONF.read_text(encoding="utf-8").strip())
            if v > 0:
                return v
    except Exception:
        pass
    return DEFAULT_STALE_DAYS


def now_tokens():
    """Текущие приоритеты из STATE `## now` (lowercase для сравнения)."""
    if not STATE_MD.exists():
        return []
    out, section = [], None
    for raw in STATE_MD.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            section = s[3:].strip().lower(); continue
        if section == "now" and s.startswith("-"):
            out.append(s.lstrip("- ").strip().lower())
    return out


def has_dod(name):
    """Есть ли записанный DoD для проекта (секция `## <name>` в dod.md)."""
    if not DOD_MD.exists():
        return False
    low = DOD_MD.read_text(encoding="utf-8").lower()
    return f"## {name.lower()}" in low


def main():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as tw

    thr = stale_days()
    nowt = now_tokens()
    now = datetime.now(timezone.utc)
    q = "query{tracks(first:100){edges{node{name stage anchor}}}}"
    data = tw._gql(q)
    candidates = []
    for e in data.get("tracks", {}).get("edges", []):
        n = e["node"]
        if (n.get("stage") or "").upper() != "ACTIVE":
            continue
        name = n.get("name", "")
        anchor = n.get("anchor")
        reasons = []
        age_days = None
        if anchor:
            try:
                dt = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
                age_days = round((now - dt).total_seconds() / 86400, 1)
                if age_days >= thr:
                    reasons.append(f"активность застыла: last-touch {age_days}д назад (порог {thr}д)")
            except Exception:
                pass
        else:
            reasons.append("нет last-touch (никогда не трогался по активности)")
        if name.lower() not in nowt:
            reasons.append("ACTIVE, но выпал из текущих приоритетов (## now)")
        if reasons:
            candidates.append({
                "name": name,
                "anchor_age_days": age_days,
                "has_dod": has_dod(name),
                "reasons": reasons,
            })

    print(json.dumps({
        "ok": True,
        "stale_threshold_days": thr,
        "now_priorities": nowt,
        "candidates": candidates,
        "note": ("КАНДИДАТЫ на 'last-mile', не приговор. Шики должна СПРОСИТЬ владельца: "
                 "это правда близко к финишу? какой твой DoD по проекту? что осталось ровно? ритуал сдачи? "
                 "DoD/порог/ритуал — контент владельца (самоонбординг M6), не зашивать."),
        "owner_needed": [c["name"] for c in candidates if not c["has_dod"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
