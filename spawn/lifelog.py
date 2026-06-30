#!/usr/bin/env python3.13
# lifelog.py — лайф-трекинг дня + silence-watchdog (M4, фаза S).
#
# владелец репортит статусы в телегу («иду гулять / поел / лёг / сел работать»),
# Шики их пишет и ведёт день; watchdog ловит молчание/просроченные якоря и мягко
# пингует. УМНО: молчание ≠ пропал — сверяемся с M0-активностью (если судья видит
# работу, человек за машиной — не дёргать «ты как»).
#
# record "<статус>"  — записать статус-репорт (Шики вызывает, когда владелец сказал)
# check              — silence + просроченные якоря + есть ли свежая M0-активность
# today              — лог дня
#
# Приватность: статусы — личные данные владельца в его DM, хранятся ЛОКАЛЬНО
# (~/secretary/state/day-<date>.md). M0-активность берём как факт «было/не было»
# (не сырой экран). Режим/якоря/порог — КОНТЕНТ владельца (regime.md, самоонбординг).
#
# stdlib only. Author: pluttan

import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_DIR = Path.home() / "secretary" / "state"
REGIME = STATE_DIR / "regime.md"
SILENCE_DEFAULT_MIN = 240  # 4ч; порог тюнит владелец (regime.md строка `порог-молчания: N`)


def msk_now():
    return datetime.now(timezone.utc) + timedelta(hours=3)


def day_file(d=None):
    d = d or msk_now()
    return STATE_DIR / f"day-{d.strftime('%Y-%m-%d')}.md"


def cmd_record(text):
    f = day_file()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(f"# День {msk_now().strftime('%Y-%m-%d')} (лайф-лог)\n\n", encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"- [{msk_now().strftime('%H:%M')}] {text.strip()}\n")
    print(json.dumps({"ok": True, "recorded": text.strip(), "file": str(f)}, ensure_ascii=False))


def last_entry_dt():
    f = day_file()
    if not f.exists():
        return None
    last = None
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"-\s*\[(\d{2}):(\d{2})\]", line.strip())
        if m:
            last = msk_now().replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return last


def silence_threshold():
    try:
        if REGIME.exists():
            m = re.search(r"(?i)порог-молчания\s*:\s*(\d+)", REGIME.read_text(encoding="utf-8"))
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return SILENCE_DEFAULT_MIN


def recent_m0_activity(minutes=15):
    """Была ли активность судьи за последние N минут (факт, не содержимое экрана)."""
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", "secretaryd", "--since", f"{minutes} min ago", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return any(re.search(r"\b(work|leak)", l) for l in out.splitlines())
    except Exception:
        return None


def overdue_anchors():
    """Якоря из regime.md (секция `## якоря`, строки `HH:MM <что>`), просроченные и не отмеченные сегодня."""
    if not REGIME.exists():
        return []
    txt = REGIME.read_text(encoding="utf-8")
    anchors = re.findall(r"(?m)^\s*-?\s*(\d{2}):(\d{2})\s+(.+)$", txt)
    if not anchors:
        return []
    now = msk_now()
    today_log = day_file().read_text(encoding="utf-8").lower() if day_file().exists() else ""
    out = []
    for hh, mm, what in anchors:
        at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        key = what.strip().lower().split()[0] if what.strip() else ""
        if now > at and key and key not in today_log:
            out.append({"at": f"{hh}:{mm}", "what": what.strip()})
    return out


def cmd_check():
    last = last_entry_dt()
    now = msk_now()
    silence_min = round((now - last).total_seconds() / 60) if last else None
    thr = silence_threshold()
    act = recent_m0_activity()
    silent = (silence_min is None or silence_min >= thr)
    # умно: если есть свежая M0-активность — не «пропал», даже если статусов нет
    ping_silence = silent and (act is False or act is None)
    print(json.dumps({
        "ok": True,
        "silence_minutes": silence_min,
        "threshold_min": thr,
        "recent_m0_activity": act,
        "ping_for_silence": ping_silence,
        "overdue_anchors": overdue_anchors(),
        "note": ("ping_for_silence=true → мягко «давно тебя не слышно, ты как». Если recent_m0_activity=true — "
                 "человек за машиной работает, НЕ дёргать про молчание. overdue_anchors — якоря режима просрочены "
                 "(спроси/напомни мягко). Режим/якоря/порог — контент владельца (regime.md, самоонбординг)."),
    }, ensure_ascii=False, indent=2))


def cmd_today():
    f = day_file()
    print(f.read_text(encoding="utf-8") if f.exists() else "(сегодня лог пуст)")


def main():
    a = sys.argv[1:]
    if a and a[0] == "record" and len(a) > 1:
        cmd_record(" ".join(a[1:]))
    elif a and a[0] == "check":
        cmd_check()
    elif a and a[0] == "today":
        cmd_today()
    else:
        print(json.dumps({"ok": False, "usage": "lifelog.py record <статус> | check | today"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
