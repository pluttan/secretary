#!/usr/bin/env python3.13
# board_macro.py — quick-add macro parser (ported from yougileTgBot macroParser).
# Lets the owner cram metadata into one capture line:
#   "купить молоко +B +xдом +tзавтра +d не забыть про кефир"
# Macros (stripped from the title):
#   +A/+B/+C/+D (lat or cyr +А/+Б/+В/+Г)  → priority 4/3/2/1
#   +xИмя                                  → label (repeatable)
#   +d <текст>                             → description (to end / next macro)
#   +tсегодня | +tзавтра | +tнеделя        → relative deadline
#   +t31.12.2026 | +t31.12.2026,14:00 | +t31.12| +t31.12,14:00  → explicit deadline
# Deadlines normalised to "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" (sortable for showall).
# Author: pluttan

import re
from datetime import date, timedelta

PRIO_MAP = {"a": 4, "b": 3, "c": 2, "d": 1, "а": 4, "б": 3, "в": 2, "г": 1}


def parse(text):
    res = {"title": "", "priority": 0, "labels": [], "description": "", "deadline": None}
    rem = text
    m = re.search(r'\+([ABCDabcdАБВГабвг])\b', rem)
    if m:
        res["priority"] = PRIO_MAP.get(m.group(1).lower(), 0)
        rem = rem[:m.start()] + rem[m.end():]
    res["labels"] = re.findall(r'\+x(\S+)', rem)
    rem = re.sub(r'\+x\S+', '', rem)
    dl, rem = _deadline(rem)
    if dl:
        res["deadline"] = dl
    dm = re.search(r'\+d\s+(.+?)(?=\+[a-zA-Zа-яёА-ЯЁ]|$)', rem)
    if dm:
        res["description"] = dm.group(1).strip()
        rem = rem[:dm.start()] + rem[dm.end():]
    res["title"] = " ".join(rem.split()).strip()
    return res


def _deadline(text):
    today = date.today()
    words = {r'\+t[Сс]егодня': today, r'\+t[Зз]автра': today + timedelta(days=1),
             r'\+t[Нн]едел[яю]': today + timedelta(days=7)}
    for pat, d in words.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return d.strftime('%Y-%m-%d'), text[:m.start()] + text[m.end():]
    pats = [
        (r'\+t(\d{1,2})\.(\d{1,2})\.(\d{2,4}),(\d{1,2}):(\d{2})', 'dt'),
        (r'\+t(\d{1,2})\.(\d{1,2})\.(\d{2,4})', 'dy'),
        (r'\+t(\d{1,2})\.(\d{1,2}),(\d{1,2}):(\d{2})', 'dmt'),
        (r'\+t(\d{1,2})\.(\d{1,2})', 'dm'),
    ]
    for pat, t in pats:
        m = re.search(pat, text)
        if m:
            v = _build(m, t, today)
            if v:
                return v, text[:m.start()] + text[m.end():]
    return None, text


def _build(m, t, today):
    g = m.groups()
    try:
        if t == 'dt':
            dd, mm, yy, hh, mi = g
        elif t == 'dy':
            dd, mm, yy = g
        elif t == 'dmt':
            dd, mm, hh, mi = g
            yy = today.year
        else:                                    # 'dm'
            dd, mm = g
            yy = today.year
        yy = int(yy)
        if yy < 100:
            yy += 2000
        out = f"{yy:04d}-{int(mm):02d}-{int(dd):02d}"
        if t in ('dt', 'dmt'):
            out += f" {int(hh):02d}:{int(mi):02d}"
        # validate
        date(yy, int(mm), int(dd))
        return out
    except (ValueError, TypeError):
        return None
