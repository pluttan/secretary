#!/usr/bin/env python3.13
# slugify.py — нормализация имени проекта в openclaw agent-id (slug).
# Кириллица транслитерируется (владелец зовёт проекты по-русски) → иначе пустой slug.
# stdlib only. Author: pluttan

import re

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def normalize_slug(name: str) -> str:
    """lowercase → ru-транслит → non-alnum в '-' → схлоп → усечение до 64. Грубый аналог openclaw normalizeAgentId.

    Контракт (расхождения с openclaw — намеренные, это «грубый» аналог):
    - '_' схлопывается в '-', тогда как openclaw normalizeAgentId '_' сохраняет.
      Имя с '_' даёт разные id (py 'ai-assistant' vs openclaw 'ai_assistant'); внутри
      create/kill самосогласованно, но агент, заведённый native `openclaw agents add`,
      получит иной id.
    - может вернуть '' (например 'ъь' или строка из одних знаков препинания):
      ВЫЗЫВАЮЩИЙ обязан проверить результат на пустоту перед использованием как id.
    """
    s = (name or "").strip().lower()
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    s = s[:64].strip("-")  # усечение до 64 — зеркалит openclaw normalizeAgentId .slice(0,64)
    return s


if __name__ == "__main__":
    import sys
    print(normalize_slug(" ".join(sys.argv[1:])))
