#!/usr/bin/env python3.13
# project_cmd.py — команды управления портфелем (R6, ось B = инструмент).
#
# Тонкие команды поверх twenty_client + wip_gate. Ничего не зашивает: стадии,
# деньги, фокус двигаются КОМАНДОЙ владельца (через main/тулзы), не разработчиком.
#
#   status                  — портфель: стадии + WIP-вердикт
#   stage <name> <STAGE>    — сменить стадию (ACTIVE гейтится WIP-лимитом)
#   money  <name> <amount>  — money_target
#   done   <name>           — → SHIPPED
#   freeze <name>           — → FROZEN
#   prioritize              — из ACTIVE выбрать «на чём фокус» (советует, не приказывает)
#
# stdlib only. Author: pluttan

import argparse
import contextlib
import json
import sys
from pathlib import Path

SECRETARYD_DIR = Path.home() / "secretary" / "secretaryd"
SPAWN_DIR = Path.home() / "secretary" / "spawn"
VALID_STAGES = {"BACKLOG", "ACTIVE", "FROZEN", "KILLED", "SHIPPED"}

# Настоящий stdout, захваченный ДО любых redirect: out() пишет JSON только сюда.
_REAL_STDOUT = sys.stdout


def tw():
    sys.path.insert(0, str(SECRETARYD_DIR))
    import twenty_client as t  # noqa: E402
    return t


def wg():
    sys.path.insert(0, str(SPAWN_DIR))
    import wip_gate as w  # noqa: E402
    return w


def out(d):
    # JSON-результат всегда в НАСТОЯЩИЙ stdout. Посторонние print() из
    # twenty_client/wip_gate (напр. WARN про дубликат имени) уводятся в stderr
    # (см. redirect_stdout в main): иначе они допишутся ПЕРЕД JSON и сломают
    # JSON.parse(stdout) у вызывающего плагина (index.ts).
    print(json.dumps(d, ensure_ascii=False, indent=2), file=_REAL_STDOUT)


def log_shipped(name):
    """Дописать факт доведения (→SHIPPED) в журнал для недельного зеркала M9. Best-effort."""
    try:
        from datetime import datetime, timezone
        from pathlib import Path
        jd = Path.home() / "secretary" / "journal"
        jd.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(jd / "shipped-log.ndjson", "a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "ts": ts}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _sync_agent_to_stage(name, stage):
    """Freeze/thaw the project's agent to match the stage: FROZEN/SHIPPED unload it from the
    gateway registry, ACTIVE/BACKLOG put it back. Best-effort — never breaks the stage command."""
    try:
        sys.path.insert(0, str(SPAWN_DIR))
        import agent_registry as ar  # noqa: E402
        if stage in ("FROZEN", "SHIPPED"):
            return ar.freeze_agent(name)
        if stage in ("ACTIVE", "BACKLOG"):
            return ar.thaw_agent(name)
    except Exception as e:
        return {"agent": f"skip ({type(e).__name__})"}
    return {}


def cmd_status(_):
    t = tw()
    from collections import Counter
    try:
        ts = t.list_tracks(first=200)
    except Exception as e:
        # Twenty недоступен → деградируем мягко (как wip_gate fail-open), а не
        # рушим весь status трейсбеком. В stderr — только класс ошибки (net/http),
        # без api-key и экранных данных.
        print(f"[project_cmd] twenty unavailable: {type(e).__name__}", file=sys.stderr)
        out({"ok": "partial", "twenty": "unavailable", "tracks": [], "wip": wg().check()})
        return
    by = Counter((x.get("stage") or "?") for x in ts)
    out({"ok": True, "total": len(ts), "by_stage": dict(by),
         "tracks": [{"name": x.get("name"), "stage": x.get("stage")} for x in ts],
         "wip": wg().check()})


def cmd_stage(a):
    stage = a.stage.upper()
    if stage not in VALID_STAGES:
        raise SystemExit(f"FATAL: стадия {stage} не из {sorted(VALID_STAGES)}")
    # KILLED — это не просто флаг в Twenty. Реальное убийство (эпитафия, архив
    # workspace, снятие agentDir, удаление из agents.list) делает kill_project.py.
    # Через stage мы бы лишь флипнули стадию → портфель «KILLED», а агент живёт.
    if stage == "KILLED":
        out({"ok": False, "error": "use_kill_project",
             "hint": f"KILLED не через stage: убивай '{a.name}' через kill_project.py "
                     f"(эпитафия + архив workspace + снятие agentDir + agents.list)."})
        return
    t = tw()
    # Команда мутирует СУЩЕСТВУЮЩИЙ проект. Без этой проверки опечатка в имени
    # ушла бы в upsert и тихо создала фантомный Track (см. create_project.py).
    if t.find_by_name(a.name) is None:
        out({"ok": False, "error": "no_such_project",
             "hint": f"Нет проекта '{a.name}'. Создавай через create_project, не плоди фантом через stage."})
        return
    if stage == "ACTIVE":
        # ВНИМАНИЕ: гейт advisory, не транзакционен. check() читает счётчик ACTIVE,
        # set_stage пишет отдельным шагом — между ними окно гонки (TOCTOU). Опора на
        # единственного писателя (apply_steps сериализует мутации); на одиночном
        # пользователе окно мало. Транзакционность требовала бы server-side
        # uniqueness в Twenty, которого нет (см. twenty_client.find_by_name).
        v = wg().check(exclude=a.name)
        if not v["available"]:
            out({"ok": False, "blocked": "wip", "wip": v,
                 "hint": f"Не активирую '{a.name}': {v['reason']}"})
            return
    node = t.set_stage(a.name, stage)
    if stage == "SHIPPED":
        log_shipped(a.name)
    agent = _sync_agent_to_stage(a.name, stage)
    out({"ok": True, "name": a.name, "stage": stage, "id": (node or {}).get("id"), "agent": agent})


def cmd_money(a):
    # money_target хранится в целых единицах валюты (twenty: int(amt)*1_000_000),
    # поэтому валидируем явно: дробь молча терялась бы, отрицательная/ноль — мусор.
    if a.amount <= 0:
        out({"ok": False, "error": "bad_amount",
             "hint": f"Сумма должна быть > 0 (получено {a.amount})."})
        return
    if a.amount != int(a.amount):
        out({"ok": False, "error": "bad_amount",
             "hint": f"Сумма — целое число единиц валюты, без дробной части (получено {a.amount})."})
        return
    currency = (a.currency or "").upper()   # 'rub' → 'RUB': иначе gql-ошибка currencyCode
    t = tw()
    if t.find_by_name(a.name) is None:
        out({"ok": False, "error": "no_such_project",
             "hint": f"Нет проекта '{a.name}'. Создавай через create_project, не плоди фантом через money."})
        return
    # twenty_client ждёт КОРТЕЖ (сумма, валюта); moneyTarget = тип CURRENCY.
    node = t.upsert_track(a.name, money_target=(a.amount, currency))
    out({"ok": True, "name": a.name, "money_target": f"{int(a.amount)} {currency}", "id": (node or {}).get("id")})


def cmd_done(a):
    t = tw()
    if t.find_by_name(a.name) is None:
        out({"ok": False, "error": "no_such_project",
             "hint": f"Нет проекта '{a.name}'. done только по существующему."})
        return
    t.set_stage(a.name, "SHIPPED")
    log_shipped(a.name)
    agent = _sync_agent_to_stage(a.name, "SHIPPED")
    out({"ok": True, "name": a.name, "stage": "SHIPPED", "agent": agent, "note": "довёл — поздравляю, это и есть лекарство"})


def cmd_freeze(a):
    t = tw()
    if t.find_by_name(a.name) is None:
        out({"ok": False, "error": "no_such_project",
             "hint": f"Нет проекта '{a.name}'. freeze только по существующему."})
        return
    t.set_stage(a.name, "FROZEN")
    agent = _sync_agent_to_stage(a.name, "FROZEN")
    out({"ok": True, "name": a.name, "stage": "FROZEN", "agent": agent})


def _money_targets(t):
    """name -> moneyTarget по трекам. list_tracks НЕ отдаёт moneyTarget (только
    id/name/stage/anchor/wipLock), поэтому деньги добираем отдельным GraphQL —
    как делает moneypath.py. Best-effort: при сбое стора возвращаем {} (деньги
    останутся None, т.е. поведение не хуже прежнего)."""
    q = ("query{tracks(first:200){edges{node{"
         "name moneyTarget{amountMicros currencyCode}}}}}")
    try:
        data = t._gql(q)
    except Exception as e:
        print(f"[project_cmd] money fetch skipped: {type(e).__name__}", file=sys.stderr)
        return {}
    return {e["node"].get("name"): e["node"].get("moneyTarget")
            for e in data.get("tracks", {}).get("edges", [])}


def cmd_prioritize(_):
    """Из ACTIVE советует фокус. Эвристика прозрачна: wip_lock > есть money_target > имя."""
    t = tw()
    act = [x for x in t.list_tracks(first=200, stage="ACTIVE")]
    # list_tracks не тянет moneyTarget — добираем отдельно и вклеиваем в трек,
    # иначе второй ключ сортировки и поле в выводе всегда None (эвристика мертва).
    money = _money_targets(t)
    for x in act:
        x["moneyTarget"] = money.get(x.get("name"))
    def rank(x):
        return (0 if x.get("wipLock") else 1,
                0 if x.get("moneyTarget") else 1,
                (x.get("name") or "").lower())
    ranked = sorted(act, key=rank)
    order = [{"name": x.get("name"), "wip_lock": bool(x.get("wipLock")),
              "money_target": x.get("moneyTarget")} for x in ranked]
    out({"ok": True, "focus": (order[0]["name"] if order else None),
         "order": order, "active_count": len(act),
         "note": "совет, не приказ; источник истины по приоритетам — STATE '## now'"})


def main():
    ap = argparse.ArgumentParser(description="Команды портфеля (R6)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("stage"); p.add_argument("name"); p.add_argument("stage"); p.set_defaults(fn=cmd_stage)
    p = sub.add_parser("money"); p.add_argument("name"); p.add_argument("amount", type=float); p.add_argument("--currency", default="RUB"); p.set_defaults(fn=cmd_money)
    p = sub.add_parser("done"); p.add_argument("name"); p.set_defaults(fn=cmd_done)
    p = sub.add_parser("freeze"); p.add_argument("name"); p.set_defaults(fn=cmd_freeze)
    sub.add_parser("prioritize").set_defaults(fn=cmd_prioritize)
    a = ap.parse_args()
    # Любой посторонний stdout из twenty_client/wip_gate (напр. WARN про дубликат
    # имени) уводим в stderr — на настоящем stdout остаётся только JSON из out().
    with contextlib.redirect_stdout(sys.stderr):
        a.fn(a)


if __name__ == "__main__":
    main()
