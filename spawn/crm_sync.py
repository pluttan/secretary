#!/usr/bin/env python3.13
# crm_sync.py — SHARED-тулза CRM↔Bot: атомарная ДВОЙНАЯ запись (R7, ось B).
#
# Один вызов держит в синхроне ДВА стора:
#   CRM  = Twenty Track (stage/money) — витрина портфеля
#   Bot  = `## now` в STATE секретаря (~/secretary/state/STATE.md) — что M0 считает
#          текущими приоритетами/якорями (secretaryd читает каждый heartbeat)
# Правило синка: stage=ACTIVE → токен проекта В `## now`; FROZEN/SHIPPED/KILLED/BACKLOG
# → УБРАТЬ из `## now`. Так CRM и наг-лист бота не расходятся.
#
# Атомарность (два разных стора → настоящего 2PC нет): пишем Twenty (запомнив
# прежнюю стадию для отката) → патчим STATE; если патч STATE упал — откатываем
# Twenty на прежнюю стадию. Если Twenty упал — STATE не трогаем.
#
# --state-file переопределяет цель (для тестов: НЕ трогать живой `## now`, его
# читает secretaryd → тестовый токен стал бы реальным якорем M0).
#
# stdlib only. Author: pluttan

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
SPAWN_DIR = Path.home() / "secretary" / "spawn"
DEFAULT_STATE = Path.home() / "secretary" / "state" / "STATE.md"
DROP_STAGES = {"FROZEN", "SHIPPED", "KILLED", "BACKLOG"}   # убрать из ## now
ADD_STAGES = {"ACTIVE"}                                    # добавить в ## now
VALID_STAGES = {"BACKLOG", "ACTIVE", "FROZEN", "KILLED", "SHIPPED"}


def tw():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as t  # noqa: E402
    return t


def wg():
    sys.path.insert(0, str(SPAWN_DIR))
    import wip_gate as w  # noqa: E402
    return w


def _silent(fn, *args, **kwargs):
    """Вызвать twenty_client-функцию, уведя её диагностику (напр. plain-text WARN о
    дублях имён) в stderr: единственный вывод crm_sync на stdout — финальный JSON,
    вызывающий делает JSON.parse(stdout) и подавится посторонней строкой."""
    with contextlib.redirect_stdout(sys.stderr):
        return fn(*args, **kwargs)


def _wip_cap() -> int:
    """Лимит `## now` берём из настраиваемого wip_gate (а не магической 3); 3 — fallback."""
    try:
        return wg().wip_limit()
    except Exception as e:
        print(f"[crm_sync] wip_limit недоступен ({type(e).__name__}), cap=3", file=sys.stderr)
        return 3


def _atomic_write(path: Path, text: str):
    """Запись через временный файл в той же директории + os.replace (атомарный rename).
    secretaryd читает STATE.md на каждом heartbeat — нельзя дать ему увидеть усечённый
    файл в окне truncate+write (иначе пустые/битые M0-якоря на тик)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".crm_sync.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_now(state_file: Path):
    """Список токенов `## now` (для отчёта/отката). Пусто если файла нет."""
    if not state_file.exists():
        return []
    now, section = [], None
    for raw in state_file.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("## "):
            section = s[3:].strip().lower(); continue
        if section == "now" and s.startswith("-"):
            now.append(_bullet_token(s))
    return now


def _bullet_token(line: str):
    """Токен из строки-буллета `## now` (или None, если не буллет). Точный одноразовый
    срез префикса '- '/'-', НЕ lstrip('- ') — тот как char-class съел бы ведущие '-'
    самого токена (напр. имя, начинающееся с дефиса)."""
    s = line.strip()
    if not s.startswith("-"):
        return None
    s = s[2:] if s.startswith("- ") else s[1:]
    return s.strip()


def patch_now(state_file: Path, token: str, add: bool, dry: bool):
    """Добавить/убрать ОДИН токен в секции `## now`, сохранив ВСЁ прочее (комментарии,
    свободный текст, отступы под-буллетов, многострочные <!-- -->) ДОСЛОВНО — секция
    больше не пересобирается по whitelist, иначе хэнд-эдит владельца молча терялся бы."""
    lines = state_file.read_text(encoding="utf-8").splitlines()
    # границы секции ## now
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == "## now"), -1)
    if start < 0:
        raise RuntimeError("в STATE нет секции '## now'")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")), len(lines))
    section = lines[start + 1:end]                       # содержимое секции — храним дословно
    items = [tok for tok in (_bullet_token(l) for l in section) if tok is not None]
    changed = False
    new_section = list(section)
    if add and token not in items:
        # новый буллет — сразу после последнего существующего (иначе в начало секции)
        last = max((i for i, l in enumerate(new_section) if _bullet_token(l) is not None), default=-1)
        new_section.insert(last + 1, f"- {token}")
        changed = True
    elif (not add) and token in items:
        # удаляем ТОЛЬКО строки-буллеты, равные токену; остальное не трогаем
        new_section = [l for l in new_section if _bullet_token(l) != token]
        changed = True
    items_after = [tok for tok in (_bullet_token(l) for l in new_section) if tok is not None]
    new_lines = lines[:start + 1] + new_section + lines[end:]
    if not dry and changed:
        _atomic_write(state_file, "\n".join(new_lines) + "\n")
    cap = _wip_cap()
    return {"changed": changed, "now_after": items_after, "cap": cap, "over_cap": len(items_after) > cap}


def main():
    ap = argparse.ArgumentParser(description="shared CRM↔Bot double-write (R7)")
    ap.add_argument("--project", required=True, help="имя проекта = токен ## now = имя Track")
    ap.add_argument("--stage", help="BACKLOG|ACTIVE|FROZEN|SHIPPED|KILLED")
    ap.add_argument("--money", type=float, help="money_target")
    ap.add_argument("--currency", default="RUB")
    ap.add_argument("--note", default="")
    ap.add_argument("--state-file", default=str(DEFAULT_STATE))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    state_file = Path(a.state_file).expanduser()
    name = a.project.strip()
    dry = a.dry_run
    res = {"ok": True, "project": name, "dry_run": dry, "state_file": str(state_file)}
    if a.note:
        # заметка получена → эхо в результат (раньше молча отбрасывалась). НЕ персистим
        # её в Twenty/STATE: никаких screen-derived (ocr/reason/window-title) в сторах.
        res["note"] = a.note

    if a.stage:
        stage = a.stage.upper()
        if stage not in VALID_STAGES:
            raise SystemExit(f"FATAL: стадия {stage} не из {sorted(VALID_STAGES)}")
    else:
        stage = None

    # дробная сумма усекается (twenty_client пишет int*1e6) — раньше молча; теперь предупреждаем
    if a.money is not None and a.money != int(a.money):
        print(f"[crm_sync] WARN: дробная сумма {a.money} усекается до {int(a.money)} {a.currency}",
              file=sys.stderr)

    # WIP-гейт на АКТИВАЦИЮ: crm_sync не должен быть вторым, негейтованным путём
    # активации проекта (тот же жёсткий гейт, что `project_cmd stage <name> ACTIVE`, R6).
    if stage in ADD_STAGES:
        try:
            v = _silent(wg().check, exclude=name)
        except Exception as e:
            # гейт не должен ронять синк: при недоступном wip_gate — fail-open (как сам гейт)
            v = {"available": True, "reason": f"wip_gate недоступен ({type(e).__name__}) — fail-open"}
            print(f"[crm_sync] wip_gate недоступен: {type(e).__name__}", file=sys.stderr)
        if not v["available"]:
            res["ok"] = False
            res["blocked"] = "wip"
            res["wip"] = v
            res["hint"] = f"Не активирую '{name}': {v['reason']}"
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return

    t = tw()
    # прежняя стадия (для отката)
    prior = None
    try:
        node = _silent(t.find_by_name, name)
        prior = (node or {}).get("stage")
    except t.TwentyError as e:
        # узко: сетевой/GraphQL сбой чтения prior. НЕ глотаем молча — фиксируем, чтобы
        # откат ниже и оператор знали, что baseline стадии для отката неизвестен.
        res["prior_lookup_failed"] = type(e).__name__
        print(f"[crm_sync] чтение prior-стадии упало: {type(e).__name__}", file=sys.stderr)
    res["prior_stage"] = prior

    # 1) CRM (Twenty) — ДВЕ мутации (stage + money), не атомарны: оборачиваем в try,
    #    при сбое best-effort откат закоммиченного и ВСЕГДА структурный JSON (вызывающий
    #    парсит stdout) — никогда не выпускать сырой traceback.
    crm = {}
    crm_ok = True
    if not dry:
        committed_stage = False
        try:
            if stage is not None:
                _silent(t.set_stage, name, stage)
                crm["stage"] = stage
                committed_stage = True
            if a.money is not None:
                _silent(t.upsert_track, name, money_target=(a.money, a.currency))
                crm["money_target"] = f"{int(a.money)} {a.currency}"
        except Exception as e:
            crm_ok = False
            res["ok"] = False
            res["error"] = f"crm_write: {type(e).__name__}: {e}"
            if committed_stage:
                # стадию успели сменить, а дальше упало → вернуть прежнюю; для НОВОГО
                # трека (prior=None — set_stage его создал, twenty_client не удаляет)
                # ставим нейтральную BACKLOG, чтобы не остался висеть ACTIVE без синка.
                target = prior if prior else "BACKLOG"
                try:
                    _silent(t.set_stage, name, target)
                    res["rolled_back"] = f"Twenty stage → {target} (CRM-запись упала: {type(e).__name__})"
                except Exception as re:
                    res["rolled_back"] = f"ОТКАТ НЕ УДАЛСЯ — рассинхрон! {type(re).__name__}"
            print(f"[crm_sync] CRM-запись упала: {type(e).__name__}", file=sys.stderr)
    else:
        crm = {"stage": stage, "money_target": (f"{int(a.money)} {a.currency}" if a.money is not None else None)}
    res["crm"] = crm

    # 2) Bot (## now) — только если шаг-1 не упал и стадия влияет на ## now
    if crm_ok and stage is not None:
        add = stage in ADD_STAGES
        drop = stage in DROP_STAGES
        if add or drop:
            try:
                res["bot"] = patch_now(state_file, name, add=add, dry=dry)
            except Exception as e:
                # ОТКАТ Twenty на прежнюю стадию — не оставлять рассинхрон. Для нового
                # трека (prior=None) — нейтральная BACKLOG (созданный ACTIVE-трек не удалить).
                if not dry:
                    target = prior if prior else "BACKLOG"
                    try:
                        _silent(t.set_stage, name, target)
                        res["rolled_back"] = f"Twenty → {target} (патч STATE упал: {type(e).__name__})"
                    except Exception as re:
                        res["rolled_back"] = (f"ОТКАТ НЕ УДАЛСЯ — рассинхрон! "
                                              f"STATE: {type(e).__name__}, откат: {type(re).__name__}")
                res["ok"] = False
                res["error"] = f"patch_now: {e}"
        else:
            res["bot"] = {"changed": False, "reason": f"стадия {stage} не влияет на ## now"}

    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
