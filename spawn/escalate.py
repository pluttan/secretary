#!/usr/bin/env python3.13
# escalate.py — эскалация когда мягкий тон не пробил (M15, фаза S).
#
# Если Шики мягко напоминает про что-то N раз, а воз не движется — повышаем
# регистр (ЖЁСТЧЕ, но владельцу, НЕ третьим лицам). ОГРАНИЧЕНИЕ (проверено): 2-го
# telegram-бота НЕТ (один botToken) → эскалация = жёсткий РЕЖИМ той же Шики, не
# отдельный бот. Настоящий 2-й бот = будущее (нужен 2-й токен от владельца).
#
#   escalate.py bump <концерн>   — мягкое напоминание проигнорено, +1
#   escalate.py check            — концерны на пороге (Шики идёт жёстче)
#   escalate.py reset <концерн>  — сдвинулось/решено
#   escalate.py list
#
# Хранение ~/secretary/state/escalation.md. На ЧЁМ можно жёстко + порог — контент
# владельца (самоонбординг): без его согласия жёсткий режим не включать.
# stdlib only. Author: pluttan

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ESC = Path.home() / "secretary" / "state" / "escalation.md"
THRESHOLD = 3   # после N проигнорённых мягких → жёстче (владелец тюнит)


def parse():
    # Возвращаем (концерны, порог). Порог — глобальная строка «порог: N» в шапке
    # (контент владельца, самоонбординг); нет строки → дефолтный THRESHOLD.
    if not ESC.exists():
        return {}, THRESHOLD
    out, cur, thr = {}, None, THRESHOLD
    for raw in ESC.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            cur = s[3:].strip(); out[cur] = 0; continue
        if cur:
            m = re.match(r"(?i)-?\s*уровень\s*:\s*(\d+)", s)
            if m:
                out[cur] = int(m.group(1))
        else:
            m = re.match(r"(?i)-?\s*порог\s*:\s*(\d+)", s)
            if m:
                thr = int(m.group(1))
    return out, thr


def write(d, thr=THRESHOLD):
    ESC.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Эскалация (M15) — счётчик проигнорённых мягких напоминаний по концернам",
             f"- порог: {thr}", ""]
    for c, lvl in d.items():
        lines += [f"## {c}", f"- уровень: {lvl}", ""]
    data = "\n".join(lines) + "\n"
    # атомарно: пишем во временный файл рядом + os.replace — краш не оставит усечённый/пустой файл
    fd, tmp = tempfile.mkstemp(dir=str(ESC.parent), prefix=".escalation-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, ESC)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    a = sys.argv[1:]
    d, thr = parse()
    if a and a[0] == "bump" and len(a) > 1:
        c = " ".join(a[1:]).strip()
        if not c:
            print(json.dumps({"ok": False, "error": "пустой концерн"}, ensure_ascii=False)); sys.exit(2)
        d[c] = d.get(c, 0) + 1; write(d, thr)
        print(json.dumps({"ok": True, "concern": c, "level": d[c], "escalate": d[c] >= thr}, ensure_ascii=False))
    elif a and a[0] == "reset" and len(a) > 1:
        c = " ".join(a[1:]).strip()
        if not c:
            print(json.dumps({"ok": False, "error": "пустой концерн"}, ensure_ascii=False)); sys.exit(2)
        d.pop(c, None); write(d, thr)
        print(json.dumps({"ok": True, "reset": c}, ensure_ascii=False))
    elif a and a[0] == "list":
        print(json.dumps({"ok": True, "threshold": thr, "concerns": d}, ensure_ascii=False, indent=2))
    elif a and a[0] == "check":
        hot = {c: l for c, l in d.items() if l >= thr}
        print(json.dumps({
            "ok": True, "threshold": thr, "escalated": hot,
            "note": ("escalated непусто → Шики повышает регистр ПО ЭТИМ концернам: прямее/жёстче, "
                     "но С СОГЛАСИЯ владельца (самоонбординг: на чём он РАЗРЕШАЕТ давить жёстко). НЕ третьим лицам. "
                     "Сдвинулось → reset. 2-й бот пока нет (один токен) — это жёсткий режим той же Шики."),
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": False, "usage": "escalate.py bump <c> | reset <c> | check | list"}, ensure_ascii=False))
        sys.exit(2)


if __name__ == "__main__":
    main()
