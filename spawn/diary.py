#!/usr/bin/env python3.13
# diary.py — вечерняя заметка дня + недельное зеркало (M9, фаза S).
#
# ПРИВАТНОСТЬ: НЕ читает spool inbox.ndjson (там ocr_text/window_title = СЕКРЕТНОЕ).
# Берёт только СУЖДЕНИЯ secretaryd из systemd-journal (app + work/leak = метки,
# их же шлёт M0) + портфель Twenty. Сырой экран наружу/в файл не попадает.
# Заметка — ФАКТЫ (сколько работал/отвлекался, топ-приложения, портфель), не оценки;
# редактируется владельцем. Складывается в ~/secretary/journal/<date>.md.
#
# Недельное зеркало считает ДОВЕДЕНИЯ (переходы→SHIPPED), не часы. История переходов
# ведётся в ~/secretary/journal/shipped-log.ndjson (дописывается при смене на SHIPPED;
# v1: засев текущих SHIPPED, дальше растёт). Что хочет видеть в дневнике — контент владельца.
#
# stdlib only. Печатает заметку (markdown). Флаг --save пишет в journal/<date>.md.
# Author: pluttan

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
JOURNAL_DIR = Path.home() / "secretary" / "journal"
SHIPPED_LOG = JOURNAL_DIR / "shipped-log.ndjson"

# Москва — фиксированный UTC+3 (без перехода на летнее время с 2014).
MSK = timezone(timedelta(hours=3), "MSK")

# маркер рукописной секции владельца — её при ре-сейве не затираем (см. merge_note).
NOTE_MARKER = "## заметка владельца"

# судья пишет строки вида: [HH:MM:SS] work: app=Code stuck=..s reason=...
#                          [HH:MM:SS] leak-watching: app=Telegram stuck=..s reason=...
#                          [HH:MM:SS] NUDGE-warn: app=Telegram stuck=..s reason=...
# NUDGE-warn/escalate — это тоже отвлечения (момент пинка), считаем их как leak.
# 'neutral' (судья не уверен / ошибка) сознательно НЕ учитываем — это не факт работы/отвлечения.
# Имя приложения может быть многословным (Google Chrome) — берём всё до ' stuck='.
LINE_RE = re.compile(
    r"\]\s*(?P<verdict>work|leak[\w-]*|NUDGE[\w-]*)\s*:\s*app=(?P<app>.+?)(?:\s+stuck=|\s*$)"
)


def judged_today():
    """Метки судьи за сегодня из journal (app+verdict). Без ocr/title (приватность).

    Возвращает list[(verdict, app)] при успехе или None при ошибке чтения журнала
    (чтобы build_note не путал реальный сбой с честно пустым днём).
    Окно «сегодня» — по МСК (TZ хоста = UTC), иначе ночь 00:00–03:00 МСК терялась.
    """
    try:
        proc = subprocess.run(
            ["journalctl", "--user", "-u", "secretaryd", "--since", "today", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "TZ": "Europe/Moscow"},
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"diary: journalctl не запустился: {type(e).__name__}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"diary: journalctl вернул код {proc.returncode}", file=sys.stderr)
        return None
    rows = []
    for line in proc.stdout.splitlines():
        m = LINE_RE.search(line)
        if m:
            v = m.group("verdict")
            rows.append((("work" if v == "work" else "leak"), m.group("app")))
    return rows


def portfolio():
    """Портфель из Twenty: (by_stage, shipped) при успехе или None при ошибке.

    Ловим широко осознанно: twenty_client — динамический импорт + сеть, набор
    исключений непредсказуем, а упасть всему дневнику из-за портфеля нельзя.
    None отличает «недоступен» от честного «пусто».
    """
    try:
        sys.path.insert(0, str(SECRETARYD_DIR))
        import twenty_client as tw
        ts = tw.list_tracks(first=200)
        by = Counter((t.get("stage") or "?") for t in ts)
        return dict(by), [t.get("name") for t in ts if t.get("stage") == "SHIPPED"]
    except Exception as e:
        print(f"diary: портфель недоступен: {type(e).__name__}", file=sys.stderr)
        return None


def weekly_shipped():
    """Доведения (→SHIPPED) за последние 7 дней из журнала. (имена, для зеркала)."""
    if not SHIPPED_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    names = []
    for line in SHIPPED_LOG.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            ts = datetime.fromisoformat(d["ts"].replace("Z", "+00:00"))
            if ts >= cutoff:
                names.append(d.get("name"))
        except Exception:
            continue
    return names


def build_note(date_str):
    rows = judged_today()
    judge_failed = rows is None
    rows = rows or []
    total = len(rows)
    work = sum(1 for v, _ in rows if v == "work")
    leak = total - work
    apps = Counter(a for _, a in rows).most_common(5)
    pf = portfolio()
    by_stage, shipped = pf if pf is not None else ({}, [])
    pct = (round(100 * work / total) if total else 0)

    lines = [f"# Дневник — {date_str}", ""]
    if total:
        lines.append(f"- активность: {total} замеров, работа ~{pct}% / отвлечения ~{100-pct}%")
        lines.append("- топ-приложения: " + ", ".join(f"{a} ({n})" for a, n in apps))
    elif judge_failed:
        lines.append("- активность: данные журнала недоступны (ошибка чтения, см. stderr)")
    else:
        lines.append("- активности за сегодня в журнале нет (трекер молчал / выходной)")
    if pf is None:
        lines.append("- портфель: недоступен (ошибка twenty_client, см. stderr)")
    else:
        lines.append(f"- портфель: " + (", ".join(f"{k}:{v}" for k, v in by_stage.items()) or "пусто"))
    if shipped:
        lines.append(f"- доведено (SHIPPED, всего): {', '.join(shipped)}")
    wk = weekly_shipped()
    lines.append(f"- недельное зеркало — доведений за 7 дней: {len(wk)}" + (f" ({', '.join(wk)})" if wk else " (журнал доведений копится с момента первого 'довёл')"))
    lines += ["", NOTE_MARKER, "<!-- сюда твои мысли/факты дня; Шики не зашивает, ты редактируешь -->", ""]
    return "\n".join(lines)


def merge_note(old, fresh):
    """Сохранить рукописную секцию владельца при ре-сейве (F79).

    Факт-блок берём свежий, а всё от маркера '## заметка владельца' и ниже —
    из уже существующего файла, чтобы повторный --save в тот же день не стёр
    вписанное владельцем. Если маркера нет в одном из текстов — не рискуем, отдаём свежий.
    """
    i = old.find(NOTE_MARKER)
    j = fresh.find(NOTE_MARKER)
    if i < 0 or j < 0:
        return fresh
    return fresh[:j] + old[i:]


def main():
    save = "--save" in sys.argv
    now = datetime.now(MSK)
    date_str = now.strftime("%Y-%m-%d")
    note = build_note(date_str)
    if save:
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        f = JOURNAL_DIR / f"{date_str}.md"
        if f.exists():
            note = merge_note(f.read_text(encoding="utf-8"), note)
        # атомарная запись: tmp + os.replace, чтобы прерывание не оставило обрезанный файл
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(note, encoding="utf-8")
        os.replace(tmp, f)
        print(json.dumps({"ok": True, "saved": str(f), "preview": note[:400]}, ensure_ascii=False, indent=2))
    else:
        print(note)


if __name__ == "__main__":
    main()
