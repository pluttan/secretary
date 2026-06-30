#!/usr/bin/env python3.13
# antifunnel.py — анти-воронка: дрейф от текущих приоритетов (M5, фаза S).
#
# Инверсия фокуса: не список отвлечений, а «занят НЕ тем, что в `## now`».
# Судья M0 уже метит «по приоритетам / не по приоритетам» (у него `## now` в
# промпте) — агрегируем этот сигнал за окно. Устойчивый дрейф → Шики задаёт
# ВОПРОС-РАЗВИЛКУ: «это осознанный свитч приоритета или увело? обновить `## now`?».
# НЕ дубль M0-нудж (тот душнит про залипание поштучно); M5 — про приоритеты, мягко.
#
# Приватность: берём метки судьи (verdict + есть ли «не по приоритет» в reason) +
# app — НЕ сырой ocr. Порог дрейфа — контент владельца (самоонбординг).
#
# ВЫВОД ТОЛЬКО ДЛЯ ТЕЛЕГИ ВЛАДЕЛЬЦУ: dominant_off_app = active_app (NEVER-список
# secretaryd) лежит в одном JSON рядом с now_priorities (контент STATE владельца).
# Канал Шики→владелец разрешён. При ЛЮБОМ другом потребителе (Twenty-синк /
# внешний лог) — вырезать dominant_off_app, иначе active_app утечёт наружу.
#
# stdlib only. Печатает JSON. Author: pluttan

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

STATE_DIR = Path.home() / "secretary" / "state"
ANTIFUNNEL_CONF = Path.home() / "secretary" / "spawn" / "antifunnel.conf"
WINDOW_MIN = 30
DRIFT_PCT_DEFAULT = 60   # ≥X% «не по приоритетам» за окно → развилка (владелец тюнит)
MIN_SAMPLES = 5


def drift_threshold():
    try:
        if ANTIFUNNEL_CONF.exists():
            v = int(ANTIFUNNEL_CONF.read_text(encoding="utf-8").strip())
            if 0 < v <= 100:
                return v
    except Exception:
        pass
    return DRIFT_PCT_DEFAULT


def now_priorities():
    f = STATE_DIR / "STATE.md"  # это STATE автолупа? нет — секретарский STATE:
    f = Path.home() / "secretary" / "state" / "STATE.md"
    try:
        if not f.exists():
            return []
        text = f.read_text(encoding="utf-8", errors="replace")  # битый UTF-8 не роняет main()
    except OSError as e:  # TOCTOU/права — не валим инструмент, пишем в stderr
        print(f"antifunnel: не прочитан STATE.md: {e}", file=sys.stderr)
        return []
    out, sec = [], None
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("## "):
            sec = s[3:].strip().lower(); continue
        # заголовок по префиксу: «## now (M5)» тоже = секция now (владелец правит руками);
        # требуем «- » и непустой пункт → «---»/пустышки не попадают в приоритеты
        if sec and sec.split()[0] == "now" and s.startswith("- "):
            item = s[2:].strip()
            if item:
                out.append(item)
    return out


def window_lines(minutes):
    # Возвращает (строки, ошибка|None). Ошибка ≠ пустой вывод: при провале
    # journalctl НЕ выдаём «дрейфа нет» — main помечает ok:false (F32).
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", "secretaryd", "--since", f"{minutes} min ago", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:  # нет бинаря/шины/прав/таймаут
        print(f"antifunnel: journalctl не отработал: {e}", file=sys.stderr)
        return [], str(e)
    if r.returncode != 0:
        err = (r.stderr or "").strip() or f"rc={r.returncode}"
        print(f"antifunnel: journalctl rc={r.returncode}: {err}", file=sys.stderr)
        return [], err
    return r.stdout.splitlines(), None


def main():
    try:
        thr = drift_threshold()
        lines, log_err = window_lines(WINDOW_MIN)
        total = off = 0
        off_apps = Counter()
        for l in lines:
            # work/leak* — судья; NUDGE-* (warn/escalate) тоже «не по приоритет» (F31).
            # app ловим до маркера stuck=/reason= (stuck печатается лишь при активной
            # воронке, см. secretaryd) — многословные имена не режутся по пробелу (F37).
            m = re.search(r"\b(work|leak|NUDGE)[\w-]*\s*:\s*app=(.*?)\s+(?:stuck|reason)=", l)
            if not m:
                continue
            total += 1
            verdict, app = m.group(1), m.group(2).strip()
            is_off = (verdict in ("leak", "NUDGE")) or ("не по приоритет" in l.lower())
            if is_off:
                off += 1
                if app:                       # пустой active_app не засоряет dominant
                    off_apps[app] += 1
        off_pct = round(100 * off / total) if total else None
        drift = bool(total >= MIN_SAMPLES and off_pct is not None and off_pct >= thr)
        dominant = off_apps.most_common(1)[0][0] if off_apps else None
        out = {
            "ok": log_err is None,            # F32: провал чтения логов ≠ «дрейфа нет»
            "drift": drift,
            "off_priority_pct": off_pct,
            "threshold_pct": thr,
            "window_min": WINDOW_MIN,
            "samples": total,
            "dominant_off_app": dominant,
            "now_priorities": now_priorities(),
            "note": ("drift=true → Шики мягкий ВОПРОС-РАЗВИЛКА: «ты в основном в "
                     f"{dominant or '<не-том>'}, а в приоритетах {now_priorities() or '—'}. это осознанный свитч "
                     "или увело? обновить `## now` или вернёмся?». НЕ душнить (это делает M0), а спросить про приоритеты. "
                     "Порог дрейфа — antifunnel.conf, контент владельца (самоонбординг: что для него 'увело' vs свитч)."),
        }
        if log_err is not None:               # логи не прочитаны → причина наружу, drift невалиден
            out["error"] = f"journalctl: {log_err}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
    except Exception as e:                    # F33: верхний предохранитель — машинно-читаемый {ok:false}, не пустой stdout
        import traceback
        traceback.print_exc()
        print(json.dumps({"ok": False, "error": f"antifunnel: {type(e).__name__}: {e}"},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
