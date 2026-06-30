#!/usr/bin/env python3.13
# shelf.py — валидация-гейт «на полку» (M8, фаза S).
#
# Анти-самообман по спросу: при входе в стадию владелец ставит ДАТУ-ГЕЙТ на внешний
# сигнал спроса («к 15.07 — 5 платящих бет»). Нет сигнала к дате → Шики поднимает
# развилку: на полку (FROZEN) или продлить/пересмотреть. Чтоб проект не висел
# «активным» вечно на одной вере.
#
#   shelf.py set <project> <YYYY-MM-DD> <сигнал...>   — поставить/обновить гейт
#   shelf.py got <project>                            — сигнал получен (снять гейт)
#   shelf.py check                                    — просроченные гейты (для Шики)
#   shelf.py list                                     — все гейты
#
# Хранение: ~/secretary/state/shelf.md, секции `## <project>` (дата/сигнал/статус).
# Какой сигнал=валидация и дата — КОНТЕНТ владельца (самоонбординг). stdlib only.
# Author: pluttan

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SHELF = Path.home() / "secretary" / "state" / "shelf.md"


def msk_today():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def parse():
    """{project: {date, signal, status}} из shelf.md."""
    if not SHELF.exists():
        return {}
    out, cur = {}, None
    for raw in SHELF.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            cur = s[3:].strip(); out[cur] = {"date": None, "signal": None, "status": "ожидание"}
            continue
        if cur:
            m = re.match(r"(?i)-?\s*дата\s*:\s*(\S+)", s)
            if m: out[cur]["date"] = m.group(1)
            m = re.match(r"(?i)-?\s*сигнал\s*:\s*(.+)", s)
            if m: out[cur]["signal"] = m.group(1).strip()
            m = re.match(r"(?i)-?\s*статус\s*:\s*(.+)", s)
            if m: out[cur]["status"] = m.group(1).strip().lower()
    return out


def write(data):
    SHELF.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Валидация-гейты «на полку» (M8)", "",
             "Дата-гейт на сигнал спроса по проекту. Контент (сигнал+дата) — со слов владельца.", ""]
    for proj, g in data.items():
        lines += [f"## {proj}", f"- дата: {g.get('date') or '?'}",
                  f"- сигнал: {g.get('signal') or '?'}", f"- статус: {g.get('status') or 'ожидание'}", ""]
    SHELF.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    a = sys.argv[1:]
    data = parse()
    if a and a[0] == "set" and len(a) >= 4:
        proj, date, signal = a[1], a[2], " ".join(a[3:])
        data[proj] = {"date": date, "signal": signal, "status": "ожидание"}
        write(data); print(json.dumps({"ok": True, "set": proj, "date": date, "signal": signal}, ensure_ascii=False))
    elif a and a[0] == "got" and len(a) >= 2:
        proj = a[1]
        if proj in data:
            data[proj]["status"] = "получен"; write(data)
            print(json.dumps({"ok": True, "got": proj}, ensure_ascii=False))
        else:
            print(json.dumps({"ok": False, "error": f"нет гейта по {proj}"}, ensure_ascii=False))
    elif a and a[0] == "list":
        print(json.dumps({"ok": True, "gates": data}, ensure_ascii=False, indent=2))
    elif a and a[0] == "check":
        today = msk_today()
        overdue = []
        for proj, g in data.items():
            if g.get("status") != "ожидание" or not g.get("date"):
                continue
            try:
                d = datetime.strptime(g["date"], "%Y-%m-%d").date()
                if d < today:
                    overdue.append({"project": proj, "date": g["date"], "signal": g["signal"],
                                    "days_overdue": (today - d).days})
            except Exception:
                continue
        print(json.dumps({
            "ok": True, "today": str(today), "overdue": overdue,
            "note": ("overdue → Шики мягкая РАЗВИЛКА: «дата-гейт по <project> истёк, сигнал '<signal>' не пришёл → "
                     "на полку (project_cmd freeze) или продлить/пересмотреть? (shelf.py set новой датой / got если пришёл)». "
                     "Сигнал=валидация и дата — контент владельца (самоонбординг)."),
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "usage": "shelf.py set <proj> <YYYY-MM-DD> <сигнал> | got <proj> | check | list"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
