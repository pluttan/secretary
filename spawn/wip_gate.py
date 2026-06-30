#!/usr/bin/env python3.13
# wip_gate.py — портфельный WIP-гейт (R6, ось B = инструмент).
#
# Лекарство от распыления владельца: не давать держать слишком много ACTIVE
# проектов одновременно. Метрика = число Track stage=ACTIVE в Twenty.
# Гейт жёсткий на АКТИВАЦИЮ (set_stage→ACTIVE), мягкий на спавн (новый в BACKLOG).
#
# Лимит НЕ зашит намертво: дефолт 3 (типичные «2-3 параллельно» владельца),
# переопределяется аргументом/конфигом ~/secretary/spawn/wip.conf (одна строка: число).
#
# stdlib only. Author: pluttan

import sys
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
WIP_CONF = Path.home() / "secretary" / "spawn" / "wip.conf"
DEFAULT_LIMIT = 3


def wip_limit() -> int:
    """Лимит из ~/secretary/spawn/wip.conf (если есть), иначе дефолт.

    Допустимо только положительное целое. Мусор / 0 / отрицательное больше НЕ
    откатывается молча на дефолт (так опечатка бесшумно меняла фактический
    лимит) — пишем предупреждение в stderr. Лимит 0 («заморозь всё») не
    поддерживается: замораживай через стадии треков, а не через нулевой лимит.
    """
    try:
        if WIP_CONF.exists():
            v = int(WIP_CONF.read_text(encoding="utf-8").strip())
            if v > 0:
                return v
            print(f"[wip_gate] wip.conf: лимит {v} вне диапазона (нужно >0) — "
                  f"использую дефолт {DEFAULT_LIMIT}", file=sys.stderr)
    except (OSError, ValueError) as e:
        # OSError — файл исчез/недоступен между exists() и чтением;
        # ValueError — содержимое не парсится в int. И то, и другое логируем.
        print(f"[wip_gate] wip.conf нечитаем/непарсится ({type(e).__name__}) — "
              f"использую дефолт {DEFAULT_LIMIT}", file=sys.stderr)
    return DEFAULT_LIMIT


def _tw():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as tw  # noqa: E402
    return tw


def active_names(exclude=None):
    """Имена ACTIVE-треков (best-effort): возвращает list имён либо None, если
    Twenty недоступен. None ≠ пустой список — вызывающий обязан отличать."""
    try:
        tw = _tw()
    except Exception as e:
        # клиент не импортируется — это не «Twenty недоступен», а проблема
        # окружения/установки: НЕ молчим (раньше тонуло в общем except), но
        # всё равно fail-open.
        print(f"[wip_gate] twenty_client import failed: {type(e).__name__}",
              file=sys.stderr)
        return None
    try:
        names = [t.get("name") for t in tw.list_tracks(first=200, stage="ACTIVE")]
        # Исключаем ровно ту запись, которую write-path обновит (а не создаст
        # дублем). find_by_name матчит ТОЧНО по имени, поэтому и здесь сравнение
        # точное — иначе re-activation другим регистром/пробелами проскочила бы
        # лимит и породила дубль ACTIVE.
        ex = exclude or ""
        return [n for n in names if n and n != ex]
    except tw.TwentyError:
        return None  # Twenty недоступен (сеть/http/gql) → fail-open, это норма
    except Exception as e:
        # дрейф схемы / баг (KeyError, TypeError, …) — НЕ молчим: логируем тип
        # в stderr (без секретов), но всё равно fail-open (инструмент важнее
        # портфельной бухгалтерии), чтобы не было тихого отключения гейта.
        print(f"[wip_gate] active_names unexpected {type(e).__name__}",
              file=sys.stderr)
        return None


def check(limit=None, exclude=None):
    """Можно ли добавить ещё один ACTIVE? Возвращает dict-вердикт."""
    lim = limit if limit is not None else wip_limit()
    act = active_names(exclude=exclude)
    if act is None:
        # Twenty недоступен → НЕ блокируем (fail-open: M0/инструмент важнее портфельной бухгалтерии)
        return {"available": True, "twenty": "unavailable", "limit": lim,
                "active": [], "active_count": 0, "reason": "Twenty недоступен — гейт пропущен (fail-open)"}
    avail = len(act) < lim
    return {
        "available": avail, "twenty": "ok", "limit": lim,
        "active": act, "active_count": len(act),
        "reason": ("ок, есть слот" if avail
                   else f"WIP-лимит {lim} достигнут: активны {act}. Заморозь что-то прежде чем брать новое."),
    }


if __name__ == "__main__":
    import json
    ex = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(check(exclude=ex), ensure_ascii=False, indent=2))
