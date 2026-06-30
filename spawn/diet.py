#!/usr/bin/env python3.13
# diet.py — enforcement плана питания (M14, фаза S).
#
# владелец задаёт план питания САМ. Секретарь — НЕ диетолог: не советует ЧТО есть,
# а следит за ОКНАМИ приёмов и заносом (текст/кнопки/голос), мягко напоминает при
# пропуске. План/окна — контент владельца (самоонбординг, ~/secretary/state/diet-plan.md).
#
#   diet.py record "<что съел>"  — занести приём
#   diet.py check                — пропущенные окна на сейчас (для напоминания)
#   diet.py today                — лог питания дня
#
# stdlib only. Author: pluttan

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_DIR = Path.home() / "secretary" / "state"
PLAN = STATE_DIR / "diet-plan.md"


def msk_now():
    # MSK — фиксированный UTC+3 (без перехода на летнее время с 2014): делаем
    # datetime по-настоящему aware с правильным offset, а не UTC-тегом на MSK-часах.
    return datetime.now(timezone(timedelta(hours=3)))


def day_file(d=None):
    d = d or msk_now()
    return STATE_DIR / f"diet-{d.strftime('%Y-%m-%d')}.md"


def cmd_record(text):
    f = day_file()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(f"# Питание {msk_now().strftime('%Y-%m-%d')}\n\n", encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"- [{msk_now().strftime('%H:%M')}] {text.strip()}\n")
    print(json.dumps({"ok": True, "recorded": text.strip(), "file": str(f)}, ensure_ascii=False))


def okna_section():
    """Текст секции `## окна` плана (от заголовка до следующего `## …`).
    None — файла плана нет; "" — секции нет."""
    if not PLAN.exists():
        return None
    in_sec = False
    out = []
    for ln in PLAN.read_text(encoding="utf-8").splitlines():
        if re.match(r"(?i)^\s*##\s*окна\b", ln):
            in_sec = True
            continue
        if in_sec and re.match(r"^\s*##\s+", ln):  # следующая секция — стоп
            break
        if in_sec:
            out.append(ln)
    return "\n".join(out)


def windows():
    """Окна приёмов из секции `## окна`: строки `HH:MM название`
    (часы 1–2 цифры; диапазон `HH:MM-HH:MM` → берётся левая граница).
    Битые/вне-диапазона времена пропускаются — одна строка не роняет check."""
    sec = okna_section()
    if not sec:
        return []
    wins = []
    for h, m, w in re.findall(r"(?m)^\s*-?\s*(\d{1,2}):(\d{2})(?:\s*[-–]\s*\d{1,2}:\d{2})?\s+(.+)$", sec):
        hh, mm = int(h), int(m)
        if not (0 <= hh < 24 and 0 <= mm < 60):  # опечатка типа 25:00 / 19:99 — пропустить
            print(f"diet: окно вне диапазона пропущено: {h}:{m}", file=sys.stderr)
            continue
        wins.append((hh, mm, w.strip()))
    return wins


def cmd_check():
    now = msk_now()
    f = day_file()  # один вызов: без TOCTOU и без рассинхрона на стыке суток
    try:
        logged = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        logged = ""
    wins = windows()
    if not wins:
        sec = okna_section()
        # время HH:MM в секции есть, но ни одно окно не распозналось → формат битый
        timeish = [ln for ln in (sec.splitlines() if sec else [])
                   if re.search(r"\d{1,2}:\d{2}", ln)
                   and not ln.lstrip().startswith(("<!--", "<", "#", ">"))]
        if timeish:
            print(json.dumps({"ok": True, "missed": [], "bad_format": True,
                              "note": "Секция `## окна` есть, но ни одна строка не распознана как окно. Формат: `HH:MM название` (напр. `09:00 завтрак`). Поправь diet-plan.md. НЕ советовать ЧТО есть."}, ensure_ascii=False))
        else:
            print(json.dumps({"ok": True, "missed": [], "no_plan": True,
                              "note": "План питания не задан. Самоонбординг: спроси у владельца его окна приёмов (HH:MM название) → запиши в diet-plan.md секцией `## окна`. НЕ советовать ЧТО есть."}, ensure_ascii=False))
        return
    # заносы дня с временем (cmd_record пишет `- [HH:MM] …`) → привязка к окну по времени
    entry_mins = [int(h) * 60 + int(m)
                  for h, m in re.findall(r"(?m)^\s*-\s*\[(\d{1,2}):(\d{2})\]", logged)]
    now_min = now.hour * 60 + now.minute
    wmins = sorted((hh * 60 + mm, name) for hh, mm, name in wins)
    GRACE = 20    # мин: окно не считаем пропущенным сразу при наступлении
    FRESH = 180   # мин: верхняя граница свежести — не напоминать про окно бесконечно
    missed = []
    for i, (wm, name) in enumerate(wmins):
        nb = wmins[i + 1][0] if i + 1 < len(wmins) else 24 * 60
        # окно закрыто, если в его слоте [окно, следующее окно) есть занос
        done = any(wm <= e < nb for e in entry_mins)
        # missed: слот открылся (с grace), ещё не протух (freshness) и не закрыт заносом
        if not done and wm + GRACE < now_min <= min(wm + FRESH, nb):
            missed.append({"at": f"{wm // 60:02d}:{wm % 60:02d}", "what": name})
    print(json.dumps({
        "ok": True, "missed": missed, "logged_entries": logged.count("- ["),
        "note": ("missed → мягко напомнить: «окно '<what>' прошло, занёс? что ел?» (занос: diet.py record). "
                 "НЕ душнить, НЕ советовать ЧТО есть — только enforcement окон плана владельца. Пусто → молчать."),
    }, ensure_ascii=False, indent=2))


def cmd_today():
    f = day_file()
    print(f.read_text(encoding="utf-8") if f.exists() else "(сегодня лог питания пуст)")


def main():
    a = sys.argv[1:]
    if a and a[0] == "record" and len(a) > 1:
        cmd_record(" ".join(a[1:]))
    elif a and a[0] == "check":
        cmd_check()
    elif a and a[0] == "today":
        cmd_today()
    else:
        print(json.dumps({"ok": False, "usage": "diet.py record <что> | check | today"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
