#!/usr/bin/env python3.13
# idea.py — инбокс идей (хвост R8, фаза S).
#
# Приём идей: текстом, голосом (→whisper), с отложенным напоминанием. Чтобы идея
# не терялась и не дёргала прямо сейчас — упала в инбокс, всплыла когда надо.
#
#   idea.py add "<идея>" [--defer YYYY-MM-DD]   — добавить (опц. отложить до даты)
#   idea.py voice <audiofile> [--defer ...]     — голос→whisper→добавить
#   idea.py list                                — открытые идеи
#   idea.py due                                 — отложенные, чья дата пришла (всплыли)
#   idea.py done <N>                            — закрыть идею номер N
#
# Хранение ~/secretary/state/ideas.md (человеко-редактируемо). whisper: ~/.local/bin/whisper.
# stdlib only. Author: pluttan

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

IDEAS = Path.home() / "secretary" / "state" / "ideas.md"
WHISPER = Path.home() / ".local" / "bin" / "whisper"

# Московское время как ПРАВИЛЬНЫЙ aware-offset (+03:00), а не UTC-тег с MSK-значением.
MSK = timezone(timedelta(hours=3))


def msk():
    return datetime.now(MSK)


def lines():
    if not IDEAS.exists():
        return []
    return [l for l in IDEAS.read_text(encoding="utf-8").splitlines() if l.strip().startswith("- [")]


def _atomic_write(path, text):
    # запись через временный файл + os.replace: краш в момент записи не обрежет ideas.md.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ideas.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(text, defer=None):
    if not text.strip():
        print(json.dumps({"ok": False, "error": "пустая идея"}, ensure_ascii=False))
        sys.exit(2)
    IDEAS.parent.mkdir(parents=True, exist_ok=True)
    if not IDEAS.exists():
        IDEAS.write_text("# Инбокс идей\n\n", encoding="utf-8")
    suf = f" (до: {defer})" if defer else ""
    with open(IDEAS, "a", encoding="utf-8") as f:
        f.write(f"- [ ] [{msk().strftime('%Y-%m-%d %H:%M')}] {text.strip()}{suf}\n")
    print(json.dumps({"ok": True, "added": text.strip(), "defer": defer}, ensure_ascii=False))


def transcribe(audio):
    if not WHISPER.exists():
        print("[idea] whisper не найден:", WHISPER, file=sys.stderr)
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.run([str(WHISPER), audio, "--language", "Russian", "--model", "small",
                                   "--output_dir", td, "--output_format", "txt"],
                                  capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                # реальная причина (нет модели, битый файл) — в stderr whisper, иначе отладка вслепую.
                print(f"[idea] whisper вышел с кодом {proc.returncode}: {(proc.stderr or '').strip()}", file=sys.stderr)
                return None
            txts = list(Path(td).glob("*.txt"))
            return txts[0].read_text(encoding="utf-8").strip() if txts else None
    except subprocess.TimeoutExpired:
        print("[idea] whisper таймаут (300с)", file=sys.stderr)
        return None
    except OSError as e:
        print(f"[idea] whisper не запустился: {e}", file=sys.stderr)
        return None


def main():
    a = sys.argv[1:]
    defer = None
    # '--' завершает разбор опций: всё после него — литеральный текст идеи (не съедаем --defer внутри текста).
    if "--" in a:
        sep = a.index("--"); head, tail = a[:sep], a[sep + 1:]
    else:
        head, tail = a, []
    if "--defer" in head:
        i = head.index("--defer")
        if i + 1 >= len(head):  # --defer без значения — иначе IndexError/трейсбек
            print(json.dumps({"ok": False, "error": "--defer требует дату YYYY-MM-DD"}, ensure_ascii=False))
            sys.exit(2)
        defer = head[i + 1]; head = head[:i] + head[i + 2:]
        try:
            # нормализуем к нулям (2026-7-5 → 2026-07-05): иначе strptime пропустит, а regex в due — нет,
            # и идея молча не всплывёт. Битая дата (tomorrow, 01.07.2026, 2026-13-45) → ошибка без записи.
            defer = datetime.strptime(defer, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            print(json.dumps({"ok": False, "error": f"--defer: дата '{defer}' не в формате YYYY-MM-DD"}, ensure_ascii=False))
            sys.exit(2)
    a = head + tail
    if a and a[0] == "add" and len(a) > 1:
        add(" ".join(a[1:]), defer)
    elif a and a[0] == "voice" and len(a) > 1:
        txt = transcribe(a[1])
        if txt:
            add("[голос] " + txt, defer)
        else:
            print(json.dumps({"ok": False, "error": "whisper не дал текст"}, ensure_ascii=False))
    elif a and a[0] == "list":
        ls = lines()
        # явная 1-based нумерация = тот же счётчик, что у `done N` → однозначная привязка в снапшоте.
        open_items = [l for l in ls if l.strip().startswith("- [ ]")]
        numbered = [{"n": i, "idea": l} for i, l in enumerate(open_items, 1)]
        print(json.dumps({"ok": True, "open": numbered}, ensure_ascii=False, indent=2))
    elif a and a[0] == "due":
        today = msk().strftime("%Y-%m-%d")
        due = []
        for l in lines():
            if not l.strip().startswith("- [ ]"):
                continue
            m = re.search(r"\(до:\s*(\d{4}-\d{2}-\d{2})\)", l)
            if m and m.group(1) <= today:
                due.append(l.strip())
        print(json.dumps({"ok": True, "due": due,
                          "note": "due → Шики всплывает отложенную идею: «ты откладывал: <идея> — время»."}, ensure_ascii=False, indent=2))
    elif a and a[0] == "done" and len(a) > 1:
        try:
            n = int(a[1])
        except ValueError:
            print(json.dumps({"ok": False, "error": "N должно быть числом"}, ensure_ascii=False))
            sys.exit(2)
        ls = IDEAS.read_text(encoding="utf-8").splitlines() if IDEAS.exists() else []
        cnt = 0; found = False
        for i, l in enumerate(ls):
            if l.strip().startswith("- [ ]"):
                cnt += 1
                if cnt == n:
                    ls[i] = l.replace("- [ ]", "- [x]", 1); found = True; break
        if found:  # не нашли N → файл не трогаем и не врём ok:True
            _atomic_write(IDEAS, "\n".join(ls) + "\n")
        print(json.dumps({"ok": found, "done": n}, ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "usage": "idea.py add <текст> [--defer YYYY-MM-DD] | voice <file> | list | due | done <N>"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
