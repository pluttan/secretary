#!/usr/bin/env python3.13
# moneypath.py — «дистанция до первого платящего» (M7, фаза S).
#
# The money goal should not stay an abstraction. M7 makes sure every ACTIVE project
# has a DECOMPOSED path to money (a model + the next concrete step toward the first
# paying user). The engine surfaces: which active projects have no path set (→ the
# agent asks the owner — the money model is THEIR content, never hard-code it), and
# where money_target is not set at all.
#
# Хранение: ~/secretary/state/moneypath.md, секции `## <project>`:
#   - модель: кто платит, за что, сколько
#   - ступень S0..S6 (S0 идея → S6 первый платёж получен) — дистанция до денег
#   - next: ближайший конкретный шаг
# Детектор проверяет наличие секции (`has_path`). Контент пишет Шики из диалога.
#
# stdlib only. Печатает JSON. Author: pluttan

import json
import re
import sys
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
MONEY_MD = Path.home() / "secretary" / "state" / "moneypath.md"


def section(name):
    """Вернуть текст секции `## <name>` из moneypath.md (или None)."""
    if not MONEY_MD.exists():
        return None
    txt = MONEY_MD.read_text(encoding="utf-8")
    # секции по '## ' заголовкам
    parts = re.split(r"(?m)^##\s+", txt)
    for p in parts:
        head = p.splitlines()[0].strip().lower() if p.strip() else ""
        if head == name.lower():
            body = "\n".join(p.splitlines()[1:]).strip()
            return body
    return None


def next_step(body):
    """Вытащить 'next:'/'шаг:' строку из секции, если есть."""
    if not body:
        return None
    for line in body.splitlines():
        m = re.match(r"(?i)\s*[-*]?\s*(?:next|шаг|ближайший шаг)\s*:\s*(.+)", line.strip())
        if m:
            return m.group(1).strip()
    return None


def main():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as tw

    q = "query{tracks(first:100){edges{node{name stage moneyTarget{amountMicros currencyCode}}}}}"
    data = tw._gql(q)
    rows, need_path, need_target = [], [], []
    for e in data.get("tracks", {}).get("edges", []):
        n = e["node"]
        if (n.get("stage") or "").upper() != "ACTIVE":
            continue
        name = n.get("name", "")
        mt = n.get("moneyTarget") or {}
        has_target = bool(mt.get("amountMicros"))
        body = section(name)
        has_path = body is not None
        rows.append({
            "name": name,
            "has_money_target": has_target,
            "has_path": has_path,
            "next_step": next_step(body),
        })
        if not has_path:
            need_path.append(name)
        if not has_target:
            need_target.append(name)

    print(json.dumps({
        "ok": True,
        "active": rows,
        "note": ("По проектам без has_path Шики СПРАШИВАЕТ владельца модель денег "
                 "(кто платит/за что/сколько) и раскладывает путь до первого платящего (ступени S0..S6 + next-шаг), "
                 "пишет в ~/secretary/state/moneypath.md. Модель денег — контент владельца, не зашивать. "
                 "Где есть next-шаг — Шики мягко напоминает: 'по <проект> ближайший шаг — X, сделал?'"),
        "owner_needed_path": need_path,
        "no_money_target": need_target,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
