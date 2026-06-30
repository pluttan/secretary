#!/usr/bin/env python3.13
# create_project.py — ЯДРО СПАВНА project-агента (R5, ось B = инструмент).
#
# Команда «новый проект X» (из телеги → main → tool) приземляется сюда: один
# вызов рождает выделенный ум проекта на openclaw. Скрипт делает ВСЁ кроме
# рестарта gateway (рестарт — решение вызывающего: на полигоне свободно, на
# боевом M0-чувствителен → отдельным шагом в чистое окно).
#
# Что делает (идемпотентно, можно перезапускать):
#   1) slug из имени (нормализация под openclaw agent-id; "main" зарезервирован)
#   2) workspace-<slug>/ — копия шаблона ~/secretary/templates/project-agent/
#      с подстановкой {{PROJECT_NAME}}/{{PROJECT_SLUG}}/{{CREATED_DATE}}
#      (НЕТ стокового BOOTSTRAP.md → ноль шаблонных «AI? robot?»)
#   3) agentDir agents/<slug>/agent/ — auth-profiles.json+models.json (копия main)
#   4) репо+папка проекта (git init + seed README, подпись pluttan), best-effort
#   5) Twenty Track-оболочка stage=BACKLOG (twenty_client, localhost), best-effort
#   6) АДДИТИВНАЯ запись в agents.list (json round-trip сохраняет ВСЕ поля, в
#      отличие от бинаря openclaw → безопасно и на боевом 2026.4.2-конфиге).
#      Бэкап конфига до правки.
#
# Печатает JSON-результат. restart_needed=true всегда (hot-reload в openclaw нет).
#
# stdlib only (python3.13). Секреты не логирует (Twenty Bearer живёт в twenty_client).
# Author: pluttan

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
TEMPLATE_DIR = HOME / "secretary" / "templates" / "project-agent"
TEMPLATE_FILES = ["SOUL.md", "AGENTS.md", "IDENTITY.md", "USER.md"]
SECRETARYD_DIR = HOME / "secretary" / "secretaryd"   # для import twenty_client

PROFILES = {
    "dev":  {"state": HOME / ".openclaw-dev", "model": "deepseek/deepseek-chat"},
    "prod": {"state": HOME / ".openclaw",     "model": "deepseek/deepseek-v4-pro"},
}
RESERVED = {"main"}


def log(msg):
    print(f"[create_project] {msg}", file=sys.stderr)


from slugify import normalize_slug  # ru-транслит-aware (общий с kill_project)


def subst(text: str, mapping: dict) -> str:
    for k, v in mapping.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def gothic(s: str) -> str:
    """Имя блэклеттером (Mathematical Bold Fraktur) — шапка сообщений project-агента.
    владелец хотел готический маркер «пишет ум проекта». Буквы a-z/A-Z → fraktur, прочее как есть."""
    out = []
    for ch in s:
        o = ord(ch)
        if "a" <= ch <= "z":
            out.append(chr(0x1D586 + (o - 97)))
        elif "A" <= ch <= "Z":
            out.append(chr(0x1D56C + (o - 65)))
        else:
            out.append(ch)
    return "".join(out)


def scaffold_workspace(ws: Path, mapping: dict, dry: bool):
    if not TEMPLATE_DIR.is_dir():
        raise SystemExit(f"FATAL: шаблон не найден: {TEMPLATE_DIR}")
    created = []
    if not dry:
        ws.mkdir(parents=True, exist_ok=True)
    for fn in TEMPLATE_FILES:
        src = TEMPLATE_DIR / fn
        if not src.exists():
            raise SystemExit(f"FATAL: нет файла шаблона {src}")
        dst = ws / fn
        # идемпотентность (док. п.9): повторный запуск (дозавести репо/Twenty
        # после частичного сбоя) НЕ должен затирать уже наработанную сид-персону
        # и флипать CREATED_DATE — пишем только отсутствующие файлы.
        if dst.exists():
            created.append(str(dst))
            continue
        content = subst(src.read_text(encoding="utf-8"), mapping)
        if not dry:
            dst.write_text(content, encoding="utf-8")
        created.append(str(dst))
    return created


def scaffold_agentdir(state: Path, slug: str, dry: bool):
    """agentDir + копия auth-profiles.json/models.json из main (каталог провайдеров)."""
    agentdir = state / "agents" / slug / "agent"
    main_dir = state / "agents" / "main" / "agent"
    copied = []
    if not dry:
        agentdir.mkdir(parents=True, exist_ok=True)
    for fn in ("auth-profiles.json", "models.json"):
        src = main_dir / fn
        if not src.exists():
            log(f"WARN: {src} нет — пропускаю (агент может словить 'Unknown model')")
            continue
        if not dry:
            shutil.copy2(src, agentdir / fn)
            os.chmod(agentdir / fn, 0o600)
        copied.append(fn)
    return str(agentdir), copied


def init_repo(repo: Path, name: str, dry: bool):
    """git init + seed README + initial commit (best-effort, подпись pluttan)."""
    try:
        if dry:
            return {"path": str(repo), "status": "dry-run"}
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        readme = repo / "README.md"
        if not readme.exists():
            readme.write_text(f"# {name}\n\nProject managed by its dedicated agent. Owner: pluttan.\n",
                              encoding="utf-8")
        # email задаём ЯВНО, иначе git выведет pluttan@<hostname> (утечка имени
        # машины-сборщика в историю репо, если его запушат).
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="pluttan", GIT_COMMITTER_NAME="pluttan",
                   GIT_AUTHOR_EMAIL="pluttan@users.noreply",
                   GIT_COMMITTER_EMAIL="pluttan@users.noreply")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        # коммитим только если есть что
        st = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True, env=env)
        if st.stdout.strip():
            subprocess.run(["git", "commit", "-q", "-m", f"seed {name}"],
                           cwd=repo, check=True, env=env)
        return {"path": str(repo), "status": "ok"}
    except Exception as e:
        return {"path": str(repo), "status": f"skip ({type(e).__name__}: {e})"}


def make_track(name: str, dry: bool):
    if dry:
        return {"status": "dry-run"}
    try:
        sys.path.insert(0, str(SECRETARYD_DIR))
        import twenty_client as tw  # noqa: E402
        node = tw.upsert_track(name, stage="BACKLOG")
        return {"status": "ok", "id": (node or {}).get("id")}
    except Exception as e:
        # детали в stderr для диагностики тихой деградации Twenty (twenty_client
        # санитизирует сообщения, Bearer в текст исключения не попадает).
        log(f"twenty skip: {e!r}")
        return {"status": f"skip ({type(e).__name__})"}  # в результат — без деталей: Twenty best-effort


def _atomic_write_json(path: Path, data) -> None:
    """Атомарная запись JSON: tmp в той же папке + fsync + os.replace.
    Обрыв процесса (pexec-таймаут SIGTERM, OOM, зависание pcomp) посреди записи
    не оставит усечённый openclaw.json → main стартует (M0 священен)."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # сохраняем исходные права боевого конфига (mkstemp создаёт 0600)
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def add_to_config(cfg_path: Path, slug: str, name: str, ws: Path, agentdir: str,
                  model: str, dry: bool):
    """АДДИТИВНО: append записи в agents.list. json round-trip сохраняет все поля.
    Read-modify-write сериализуется flock'ом (анти lost-update) и пишется атомарно
    (tmp+os.replace), чтобы обрыв/гонка не убили боевой openclaw.json (M0).
    NB: для полной защиты от гонки spawn↔kill тот же лок должен брать kill_project.py."""
    lock_path = cfg_path.parent / ".config.lock"
    # в dry-run лок не берём и файлов не плодим — только читаем и считаем
    lock_ctx = contextlib.nullcontext() if dry else open(lock_path, "w")
    with lock_ctx as lock_f:
        if lock_f is not None:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        agents = cfg.setdefault("agents", {})
        lst = agents.setdefault("list", [])
        # main ПЕРВЫМ (иначе первая запись станет default → смерть main).
        # Восстанавливаем ДО дедуп-выхода: если main выпал, повторный запуск обязан
        # его вернуть, а не отдать 'already-in-list' с нарушенным инвариантом.
        main_restored = False
        if not any(isinstance(a, dict) and a.get("id") == "main" for a in lst):
            lst.insert(0, {"id": "main", "default": True})
            main_restored = True
        already = any(isinstance(a, dict) and a.get("id") == slug for a in lst)
        entry = None
        if not already:
            entry = {"id": slug, "name": name, "workspace": str(ws),
                     "agentDir": agentdir, "model": model}
            lst.append(entry)
        if already and not main_restored:
            return {"status": "already-in-list"}
        if dry:
            out = {"status": "dry-run"}
            if entry is not None:
                out["would_add"] = entry
            if main_restored:
                out["main_restored"] = True
            return out
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        bak = cfg_path.with_suffix(cfg_path.suffix + f".bak-pre-spawn-{slug}-{ts}")
        shutil.copy2(cfg_path, bak)
        _atomic_write_json(cfg_path, cfg)
        status = "main-restored" if (already and main_restored) else "added"
        return {"status": status, "backup": str(bak)}


def main():
    ap = argparse.ArgumentParser(description="Спавн project-агента (R5 instrument)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--desc", default=None, help="краткое описание-сид проекта (в персону агента, {{PROJECT_DESC}})")
    ap.add_argument("--profile", choices=list(PROFILES), default="dev")
    ap.add_argument("--repo-path", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-twenty", action="store_true")
    ap.add_argument("--no-repo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    prof = PROFILES[args.profile]
    state = prof["state"]
    cfg_path = state / "openclaw.json"
    model = args.model or prof["model"]
    dry = args.dry_run

    slug = normalize_slug(args.name)
    if not slug:
        raise SystemExit("FATAL: пустой slug из имени")
    if slug in RESERVED:
        raise SystemExit(f"FATAL: '{slug}' зарезервирован")
    if not cfg_path.exists():
        raise SystemExit(f"FATAL: конфиг профиля не найден: {cfg_path}")

    ws = state / f"workspace-{slug}"
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    desc = args.desc or "(описание не задано — собери онбордингом при первом контакте)"
    mapping = {"PROJECT_NAME": args.name, "PROJECT_SLUG": slug, "CREATED_DATE": created_at,
               "PROJECT_DESC": desc, "PROJECT_GOTHIC": gothic(args.name)}
    repo = Path(args.repo_path).expanduser() if args.repo_path else (HOME / "pr" / "pets" / slug)

    log(f"profile={args.profile} slug={slug} dry={dry}")
    result = {
        "ok": True, "slug": slug, "name": args.name, "profile": args.profile,
        "workspace": str(ws), "model": model,
    }
    # Конвейер с побочными эффектами обёрнут в try: при позднем сбое (напр.
    # битый/полу-записанный openclaw.json → JSONDecodeError в add_to_config)
    # печатаем ЧАСТИЧНЫЙ результат с пройденными шагами и orphan-ресурсами,
    # чтобы накопленное не потерялось, а оператор видел что подчистить.
    try:
        result["files"] = scaffold_workspace(ws, mapping, dry)
        agentdir, copied = scaffold_agentdir(state, slug, dry)
        result["agentDir"] = agentdir
        result["auth_copied"] = copied
        result["repo"] = {"status": "skipped"} if args.no_repo else init_repo(repo, args.name, dry)
        result["twenty"] = {"status": "skipped"} if args.no_twenty else make_track(args.name, dry)
        result["config"] = add_to_config(cfg_path, slug, args.name, ws, agentdir, model, dry)
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"
        result["orphans"] = {
            "workspace": str(ws),
            "agentDir": str(state / "agents" / slug / "agent"),
            "repo": (None if args.no_repo else str(repo)),
        }
        log(f"FATAL поздний сбой: {result['error']}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)
    # WIP-гейт мягко: новый проект паркуется в BACKLOG, но если активных уже на
    # лимите — предупреждаем (анти-распыление: «заморозь что-то прежде чем активировать»).
    try:
        import wip_gate
        v = wip_gate.check()
        if v.get("twenty") == "ok" and not v.get("available"):
            result["wip_warning"] = (f"ты на WIP-лимите ({v['limit']}): активны {v['active']}. "
                                     f"'{args.name}' припаркован в BACKLOG — заморозь что-то прежде чем активировать.")
    except Exception as e:
        # не глотаем молча: тихое падение WIP-гейта = пропавшее предупреждение,
        # регресс никто не заметит. Тип ошибки — в stderr и в результат.
        log(f"wip_gate skip: {e!r}")
        result["wip_warning_error"] = type(e).__name__
    result["restart_needed"] = True
    result["restart_cmd"] = (
        "systemctl --user restart openclaw-gateway-dev.service" if args.profile == "dev"
        else "systemctl --user restart openclaw.service  # M0-окно! бэкап есть"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
