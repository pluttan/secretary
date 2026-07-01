#!/usr/bin/env python3.13
# anchors.py — unified anchor calendar (M2 DoD: "календарь якорей агрегирован"). Merges three
# sources into one time-sorted view: board card deadlines, scheduled reminders, and the recurring
# weekly grid from Расписание.md (пары/репетиторство). Grouped сегодня / завтра / эта неделя.
# stdlib only. Author: pluttan

import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_db as bd
import reminders as rem

MSK = ZoneInfo("Europe/Moscow")
SCHED = Path.home() / "silverbullet" / "space" / "Расписание.md"
DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _now():
    return datetime.now(MSK)


def _parse_dt(s):
    s = (s or "").strip()
    try:
        if len(s) <= 10:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=MSK)
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
    except ValueError:
        return None


def deadlines():
    out = []
    try:
        with bd.conn() as c:
            for cd in bd.all_cards(c, include_done=False):
                dt = _parse_dt(cd["deadline"])
                if dt:
                    out.append((dt, f"▦ {cd['title']}", cd["board"] or "доска"))
    except Exception:
        pass
    return out


def reminders_():
    out = []
    try:
        for r in rem.listing().get("reminders", []):
            dt = _parse_dt(r["due"].replace("T", " "))
            if dt:
                out.append((dt, f"⏰ {r['text']}", "напоминание"))
    except Exception:
        pass
    return out


def schedule():
    out = []
    try:
        text = SCHED.read_text()
    except Exception:
        return out
    now = _now()
    for block in text.split("##"):
        lines = [l for l in block.splitlines() if l.strip().startswith("|")]
        if len(lines) < 3:
            continue
        header = [c.strip() for c in lines[0].strip("|").split("|")]
        colday = {i: DAYS_RU.index(c) for i, c in enumerate(header) if c in DAYS_RU}
        for row in lines[2:]:                                    # skip header + separator
            cells = [c.strip() for c in row.strip("|").split("|")]
            m = re.match(r'(\d{1,2}):(\d{2})', cells[0]) if cells else None
            if not m:
                continue
            hh, mm = int(m.group(1)), int(m.group(2))
            for i, cell in enumerate(cells):
                if i in colday and cell and cell not in ("-", ""):
                    days_ahead = (colday[i] - now.weekday()) % 7
                    occ = (now + timedelta(days=days_ahead)).replace(
                        hour=hh, minute=mm, second=0, microsecond=0)
                    if occ < now:
                        occ += timedelta(days=7)
                    out.append((occ, f"📅 {cell}", "расписание"))
    return out


def collect():
    items = deadlines() + reminders_() + schedule()
    items.sort(key=lambda x: x[0])
    return items


def render():
    now = _now()
    horizon = now + timedelta(days=7)
    today, tomorrow, week = [], [], []
    for dt, label, src in collect():
        if dt > horizon or dt < now - timedelta(hours=2):
            continue
        if dt.date() == now.date():
            today.append((dt, label, src))
        elif dt.date() == (now + timedelta(days=1)).date():
            tomorrow.append((dt, label, src))
        else:
            week.append((dt, label, src))

    def fmt(lst, withdate=False):
        if not lst:
            return "  —"
        f = '%d.%m %H:%M' if withdate else '%H:%M'
        return "\n".join(f"  {dt.strftime(f)} {label}  «{src}»" for dt, label, src in lst)

    return (f"якоря ({DAYS_RU[now.weekday()]}, {now.strftime('%d.%m')}):\n"
            f"СЕГОДНЯ:\n{fmt(today)}\n"
            f"ЗАВТРА:\n{fmt(tomorrow)}\n"
            f"ЭТА НЕДЕЛЯ:\n{fmt(week, True)}")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--json":
        import json
        print(json.dumps([{"when": dt.isoformat(), "what": label, "src": src}
                          for dt, label, src in collect()], ensure_ascii=False))
        return
    print(render())


if __name__ == "__main__":
    main()
