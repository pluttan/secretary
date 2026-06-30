#!/usr/bin/env python3.13
# kill_project.py — УБИЙСТВО project-агента с эпитафией (R5, ось B = инструмент).
#
# Парный к create_project.py. «Убил проект» по плану = НЕ просто стереть, а:
#   1) написать ЭПИТАФИЮ-Note (что было, когда закрыто, почему) → graveyard/
#   2) Twenty Track → stage KILLED (best-effort)
#   3) АРХИВ workspace в graveyard/ (mv, НЕ rm — работа не теряется)
#   4) убрать agentDir (auth/models — копии, регенерируемы)
#   5) АДДИТИВНО убрать из agents.list (json round-trip + бэкап)
# Рестарт gateway не делает (как create_project): полигон свободно, боевой — в окно.
#
# Рефлексивную эпитафию своими словами агент может написать сам ДО смерти (live);
# этот движок кладёт структурную эпитафию с метаданными+причиной (+ срез его SOUL).
#
# stdlib only (python3.13). Author: pluttan

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
GRAVEYARD = HOME / "secretary" / "graveyard"
SECRETARYD_DIR = HOME / "secretary" / "secretaryd"
PROFILES = {"dev": HOME / ".openclaw-dev", "prod": HOME / ".openclaw"}
RESERVED = {"main"}


def log(m):
    print(f"[kill_project] {m}", file=sys.stderr)


# spawn/ в sys.path[0] — чтобы локальный slugify импортировался при любом способе
# запуска (модуль/обёртка с иным sys.path), а не только при прямом запуске скрипта.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from slugify import normalize_slug  # ru-транслит-aware (общий с create_project)


def write_epitaph(slug, name, reason, ws: Path, archive_dst: Path, epitaph_path: Path, dry: bool) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    soul_excerpt = ""
    soul = ws / "SOUL.md"
    if soul.exists():
        soul_excerpt = soul.read_text(encoding="utf-8")[:600]
    body = (
        f"# Эпитафия — {name}\n\n"
        f"- id: {slug}\n- закрыт: {ts}\n- причина: {reason or '(не указана)'}\n"
        f"- архив workspace: graveyard/{archive_dst.name}/\n\n"
        f"## кем был (срез SOUL)\n\n{soul_excerpt or '(SOUL не найден)'}\n\n"
        f"---\nЗакрыто инструментом по команде pluttan'а. Работа не стёрта — заархивирована.\n"
    )
    if not dry:
        GRAVEYARD.mkdir(parents=True, exist_ok=True)
        epitaph_path.write_text(body, encoding="utf-8")
    return str(epitaph_path)


def track_killed(name, dry):
    if dry:
        return {"status": "dry-run"}
    try:
        sys.path.insert(0, str(SECRETARYD_DIR))
        import twenty_client as tw  # noqa: E402
        tw.set_stage(name, "KILLED")
        return {"status": "ok"}
    except Exception as e:
        return {"status": f"skip ({type(e).__name__})"}


def remove_from_config(cfg_path: Path, slug: str, dry: bool):
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    lst = cfg.get("agents", {}).get("list", [])
    idx = next((i for i, a in enumerate(lst) if isinstance(a, dict) and a.get("id") == slug), -1)
    if idx < 0:
        return {"status": "not-in-list"}, None
    entry = lst[idx]
    if dry:
        return {"status": "dry-run", "would_remove": entry}, entry
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = cfg_path.with_suffix(cfg_path.suffix + f".bak-pre-kill-{slug}-{ts}")
    shutil.copy2(cfg_path, bak)
    del lst[idx]
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "removed", "backup": str(bak)}, entry


def archive_workspace(ws: Path, dst: Path, dry: bool):
    if not ws.exists():
        return {"status": "no-workspace"}
    if dry:
        return {"status": "dry-run", "to": str(dst)}
    GRAVEYARD.mkdir(parents=True, exist_ok=True)
    # dst уже разрешён в main() с защитой от коллизии slug — НЕ rm, только mv
    # (инвариант шапки: «mv, НЕ rm — работа не теряется»).
    shutil.move(str(ws), str(dst))
    return {"status": "archived", "to": str(dst)}


def remove_agentdir(agentdir: Path, dry: bool):
    # agentDir — копии auth/models; чистим, но НЕ глотаем сбой молча (была
    # ignore_errors=True → частичный остаток секретов без следа в выводе).
    if not agentdir.exists():
        return {"status": "absent"}  # нечего удалять — отчёт честный (не «removed»)
    if dry:
        return {"status": "dry-run", "to_remove": str(agentdir)}
    errors = []

    def onexc(func, path, exc):
        errors.append(f"{path}: {type(exc).__name__}")
        log(f"agentDir rmtree fail {path}: {type(exc).__name__}")

    shutil.rmtree(agentdir, onexc=onexc)
    if agentdir.exists() or errors:
        # частичный сбой (права/занятый файл): копия auth-profiles.json могла остаться
        return {"status": "partial", "remained": agentdir.exists(), "errors": errors}
    return {"status": "removed"}


def main():
    ap = argparse.ArgumentParser(description="Убить project-агента с эпитафией (R5)")
    ap.add_argument("--slug", help="agent-id (или используй --name)")
    ap.add_argument("--name", help="имя проекта (если не дан --slug)")
    ap.add_argument("--profile", choices=list(PROFILES), default="dev")
    ap.add_argument("--reason", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.slug and not args.name:
        raise SystemExit("FATAL: нужен --slug или --name")
    slug = args.slug or normalize_slug(args.name)
    if slug in RESERVED:
        raise SystemExit(f"FATAL: '{slug}' зарезервирован — не убиваем")

    state = PROFILES[args.profile]
    cfg_path = state / "openclaw.json"
    if not cfg_path.exists():
        raise SystemExit(f"FATAL: конфиг не найден: {cfg_path}")

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    entry = next((a for a in cfg.get("agents", {}).get("list", [])
                  if isinstance(a, dict) and a.get("id") == slug), None)
    if not entry:
        raise SystemExit(f"FATAL: агент '{slug}' не найден в agents.list ({args.profile})")
    name = entry.get("name", slug)
    # `or`, а не get(key, default): при ключе со значением null get вернул бы None
    # → Path(None) бросает TypeError (битый/ручной конфиг). `or` берёт дефолт и на None.
    ws = Path(entry.get("workspace") or (state / f"workspace-{slug}"))
    agentdir = Path(entry.get("agentDir") or (state / "agents" / slug / "agent"))
    dry = args.dry_run

    log(f"profile={args.profile} slug={slug} dry={dry}")
    result = {"ok": True, "slug": slug, "name": name, "profile": args.profile}

    # Целевые пути эпитафии и архива разрешаем ОДИН раз, с защитой от коллизии slug:
    # повторный kill после re-create того же имени НЕ должен затирать прежний архив/
    # эпитафию (инвариант шапки «mv, НЕ rm»). Существование проверяем read-only,
    # каталог не создаём — на --dry-run это чистый no-op.
    kill_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    epitaph_path = GRAVEYARD / f"{slug}.md"
    if epitaph_path.exists():
        epitaph_path = GRAVEYARD / f"{slug}-{kill_ts}.md"
    archive_dst = GRAVEYARD / f"{slug}-workspace"
    if archive_dst.exists():
        archive_dst = GRAVEYARD / f"{slug}-workspace-{kill_ts}"

    # эпитафия читает SOUL.md из ещё-живого workspace → строго ДО архива
    result["epitaph"] = write_epitaph(slug, name, args.reason, ws, archive_dst, epitaph_path, dry)
    result["twenty"] = track_killed(name, dry)
    # СНАЧАЛА (с бэкапом) убираем запись из agents.list, и ТОЛЬКО потом —
    # деструктивные fs-операции. Иначе при сбое записи конфига агент остался бы
    # в списке, но без workspace/agentDir → битая ссылка на рестарте, без отката.
    cfg_res, _ = remove_from_config(cfg_path, slug, dry)
    result["config"] = cfg_res
    result["workspace_archive"] = archive_workspace(ws, archive_dst, dry)
    result["agentDir"] = remove_agentdir(agentdir, dry)

    # рестарт нужен ТОЛЬКО если конфиг реально изменён (запись удалена). На --dry-run
    # и при not-in-list ничего не менялось → не толкаем к M0-чувствительному рестарту.
    config_changed = cfg_res.get("status") == "removed"
    result["restart_needed"] = config_changed
    restart_cmd = (
        "systemctl --user restart openclaw-gateway-dev.service" if args.profile == "dev"
        else "systemctl --user restart openclaw.service  # M0-окно!"
    )
    result["restart_cmd"] = restart_cmd if config_changed else None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
