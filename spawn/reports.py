#!/usr/bin/env python3.13
# reports.py — сухие дела + граф git-активности по проектам (хвост R8, фаза S).
#
# Считает РЕАЛЬНУЮ git-активность репозиториев проектов (коммиты по дням) →
# простой «граф квадратиков» (счётчики день/7д/30д). Репо разбросаны: часть на
# pcomp (~/work, ~/pr), часть на маке (~/pr/pets, через `ssh macair`).
#
# Карта проект→репо: ~/secretary/state/repos.md, строки `имя: [mac:]<путь>`.
# Нет карты → автодискавери кандидатов (Шики предлагает владельцу смапить — самоонбординг).
# Только git-МЕТАДАННЫЕ (даты/число коммитов), НЕ содержимое (приватность кода).
#
# stdlib only. Печатает JSON/текст. Author: pluttan

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPOS_MD = Path.home() / "secretary" / "state" / "repos.md"


def load_map():
    """{project: (host, path)} из repos.md; host='mac' или None (локально)."""
    if not REPOS_MD.exists():
        return {}
    out = {}
    for line in REPOS_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([^#:][^:]*?)\s*:\s*(?:(mac):)?(\S+)\s*$", line)
        if m and "/" in m.group(3):
            out[m.group(1).strip()] = (m.group(2), m.group(3))
    return out


def git_dates(host, path, days=30):
    """Список дат коммитов за N дней (YYYY-MM-DD). Только метаданные."""
    cmd = ["git", "-C", path, "log", f"--since={days} days ago", "--format=%cd", "--date=short"]
    try:
        if host == "mac":
            cmd = ["ssh", "-o", "ConnectTimeout=8", "macair", " ".join(cmd)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return None


def discover():
    """Кандидаты-репо: pcomp ~/work,~/pr + мак ~/pr/pets."""
    cands = []
    for base in (Path.home() / "work", Path.home() / "pr"):
        if base.is_dir():
            for g in base.glob("*/.git"):
                cands.append(str(g.parent))
    try:
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "macair", "ls -d ~/pr/pets/*/.git 2>/dev/null"],
                             capture_output=True, text=True, timeout=20).stdout
        for l in out.splitlines():
            if l.strip().endswith("/.git"):
                cands.append("mac:" + l.strip()[:-5])
    except Exception:
        pass
    return cands


def main():
    m = load_map()
    if not m:
        print(json.dumps({"ok": True, "no_map": True, "candidates": discover(),
                          "note": "Карта проект→репо пуста. Самоонбординг: Шики предлагает владельцу смапить кандидатов в "
                                  "~/secretary/state/repos.md (строки `имя: [mac:]<путь>`), затем reports считает граф."}, ensure_ascii=False, indent=2))
        return
    today = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")
    rep = {}
    for proj, (host, path) in m.items():
        dates = git_dates(host, path)
        if dates is None:
            rep[proj] = {"error": "репо недоступен (мак офлайн / нет пути)"}
            continue
        c = Counter(dates)
        rep[proj] = {
            "commits_30d": len(dates),
            "commits_7d": sum(v for d, v in c.items() if d >= (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")),
            "today": c.get(today, 0),
            "active_days_30d": len(c),
        }
    print(json.dumps({"ok": True, "today": today, "projects": rep,
                      "note": "Сухие цифры git-активности (метаданные, не код). Шики: краткая сводка «за неделю по X — N коммитов, M активных дней». Граф квадратиков = active_days."}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
