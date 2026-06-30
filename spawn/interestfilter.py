#!/usr/bin/env python3.13
# interestfilter.py — мягкий фильтр «интересно → цель» (M13, фаза S).
#
# владелец выбирает по интересу, не по цели. M13 — САМЫЙ МЯГКИЙ из всех: при НОВОМ
# проекте/увлечении Шики задаёт ОДИН лёгкий вопрос «это к цели/деньгам или просто
# интересно?» и принимает ЛЮБОЙ ответ. НЕ блок (это не wip-gate), НЕ повтор, НЕ
# душнёж. Драйв важнее — фильтр можно вообще выключить (interestfilter.off).
#
# Детект «нового» = диф текущих проектов (Twenty) с виденными ранее
# (~/secretary/state/.seen-projects.json). Новый → один раз пометить «спросить».
#
# stdlib only. Печатает JSON. Author: pluttan

import json
import os
import sys
from pathlib import Path

STATE_DIR = Path.home() / "secretary" / "state"
SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
SEEN = STATE_DIR / ".seen-projects.json"
OFF = Path.home() / "secretary" / "spawn" / "interestfilter.off"


def load_seen():
    # None = РАБОЧЕГО baseline нет (нет файла / битый / пустой) → нужен молчаливый
    # базлайн, а не спрос оптом. Пустой/битый трактуем как отсутствие baseline.
    try:
        data = json.loads(SEEN.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:  # битый JSON / нечитаемый файл
        print(f"interestfilter: .seen-projects.json не прочитан ({e}) — трактую как отсутствие baseline", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print("interestfilter: .seen-projects.json не список — трактую как отсутствие baseline", file=sys.stderr)
        return None
    s = set(data)
    # пустой baseline = отсутствие baseline (иначе на след. прогоне всё уйдёт в new оптом)
    return s if s else None


def save_seen(s):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # атомарная запись: пишем во временный файл и подменяем, чтобы убитый
    # на полузаписи процесс не оставил битый baseline.
    tmp = SEEN.parent / (SEEN.name + ".tmp")
    tmp.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SEEN)


def current_projects():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as tw
    return [t.get("name") for t in tw.list_tracks(first=200) if t.get("name")]


def main():
    # bandit на тоне / выключение — драйв важнее фильтра
    if OFF.exists():
        print(json.dumps({"ok": True, "disabled": True, "new_projects": [],
                          "note": "фильтр выключен (interestfilter.off) — драйв важнее, не спрашивать."}, ensure_ascii=False))
        return
    seen = load_seen()
    # first_run = нет РАБОЧЕГО baseline (нет файла / битый / пустой), а не просто
    # отсутствие файла — иначе битый/пустой .seen-projects.json пометил бы всё в new оптом.
    first_run = seen is None
    cur = current_projects()
    # первый прогон = базлайн: пометить всё виденным молча (не спрашивать оптом про существующее)
    new = [] if first_run else [p for p in cur if p not in seen]
    save_seen((seen or set()) | set(cur))
    if first_run:
        print(json.dumps({"ok": True, "first_run_baseline": True, "new_projects": [],
                          "note": "первый прогон: текущие проекты помечены виденными, не спрашиваю оптом. Новые ловятся со следующего раза."}, ensure_ascii=False))
        return
    print(json.dumps({
        "ok": True,
        "new_projects": new,
        "note": ("По каждому new_projects Шики задаёт ОДИН лёгкий вопрос: «<проект> — это к цели/деньгам "
                 "или просто интересно? оба ок, просто чтоб ты сам видел». Принять ЛЮБОЙ ответ, НЕ блокировать, "
                 "НЕ повторять, НЕ душнить. Если владелец сказал 'не лезь с этим' — создать interestfilter.off. "
                 "Это самый мягкий фильтр: драйв важнее. (первый прогон помечает все текущие как виденные — не спрашивать оптом.)"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
