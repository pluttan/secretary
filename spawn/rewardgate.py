#!/usr/bin/env python3.13
# rewardgate.py — гейт наградного слоя (M3, фаза S). ЧЕСТНО: МЯГКИЙ.
#
# ОГРАНИЧЕНИЕ (проверено): авто-пауза музыки с pcomp НЕВОЗМОЖНА — Navidrome это
# СЕРВЕР (:4533), играет клиент владельца (мак/телефон), pcomp его не контролирует
# (playerctl без плееров — headless). Поэтому гейт = СИГНАЛ + словесное придержание
# Шики, не принудительная остановка. Честный «honor-system» гейт.
#
# Сигнал: по последним меткам судьи (work/leak) + флагу `## gate` в STATE секретаря.
# reward_earned=true → работал, награда «заслужена»; false → залипает, Шики мягко
# придерживает («домузицируешь после того как добьёшь X»). Правила награды/порога —
# контент владельца (самоонбординг).
#
# stdlib only. Печатает JSON. Author: pluttan

import json
import re
import subprocess
import sys
from pathlib import Path

STATE_MD = Path.home() / "secretary" / "state" / "STATE.md"
WINDOW_MIN = 30


def gate_flag():
    """## gate в STATE секретаря: on (энфорсит) | off (вето, молчит)."""
    if not STATE_MD.exists():
        return "on"
    sec = None
    for raw in STATE_MD.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            sec = s[3:].strip().lower(); continue
        if sec == "gate" and s and not s.startswith("<!--"):
            return "off" if s.lower() == "off" else "on"
    return "on"


def work_leak_window(minutes=WINDOW_MIN):
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", "secretaryd", "--since", f"{minutes} min ago", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return 0, 0
    work = leak = 0
    for l in out.splitlines():
        m = re.search(r"\b(work|leak)", l)
        if m:
            if m.group(1) == "work":
                work += 1
            else:
                leak += 1
    return work, leak


def main():
    gate = gate_flag()
    work, leak = work_leak_window()
    total = work + leak
    work_pct = round(100 * work / total) if total else None
    # «заслужил» если в окне преобладала работа; нет данных → не гейтить (не наказывать вслепую)
    earned = (work_pct is None) or (work_pct >= 50)
    print(json.dumps({
        "ok": True,
        "reward_earned": earned,
        "gate_flag": gate,
        "window_min": WINDOW_MIN,
        "work": work, "leak": leak, "work_pct": work_pct,
        "control": "soft-only",
        "note": ("Авто-пауза НЕвозможна (Navidrome сервер, играет клиент владельца). Гейт словесный: "
                 "reward_earned=false (залипает) + gate_flag=on → Шики МЯГКО придерживает награду "
                 "('домузицируешь, как добьёшь X'); earned=true → не трогать, заслужил. "
                 "gate_flag=off → вето, молчать. Правила награды/порога — контент владельца (самоонбординг)."),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
