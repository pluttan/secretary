#!/usr/bin/env python3.13
# agent_registry.py — reversible freeze/thaw of a project agent in the openclaw registry.
#
# Problem: a project's stage (Twenty Track: BACKLOG/ACTIVE/FROZEN/SHIPPED) and the agent's
# presence in openclaw `agents.list` are decoupled. An agent in the list is loaded by the
# gateway and stays "live" forever — a frozen/shipped/abandoned project keeps a live agent.
# kill_project is the only removal, and it is destructive (epitaph + archive + agentDir wipe).
#
# This adds a middle state: FREEZE moves the agent's entry out of `agents.list` into a frozen
# store (~/.openclaw/agents-frozen.json) — the gateway stops loading it — while agentDir and
# workspace stay intact. THAW puts it back. Fully reversible, unlike kill. A gateway restart
# applies the change (openclaw has no hot-reload).
#
#   agent_registry.py freeze <name> [--dry]
#   agent_registry.py thaw   <name> [--dry]
#   agent_registry.py list                  — active (in registry) + frozen
# stdlib only. Author: pluttan

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
FROZEN_STORE = Path.home() / ".openclaw" / "agents-frozen.json"


def _atomic_write(path: Path, data):
    # tempfile in the same dir + os.replace → never leave a half-written registry
    # (a torn openclaw.json would kill the gateway on next start).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _find_idx(lst, name):
    return next((i for i, a in enumerate(lst)
                 if isinstance(a, dict) and (a.get("id") == name or a.get("name") == name)), -1)


def freeze_agent(name, dry=False):
    """Move the project agent out of agents.list into the frozen store. agentDir/workspace
    stay intact — reversible. Never freezes the default agent (main). Idempotent."""
    if not OPENCLAW_CFG.exists():
        return {"agent": "no-config"}
    cfg = _load(OPENCLAW_CFG, {})
    lst = cfg.get("agents", {}).get("list", [])
    idx = _find_idx(lst, name)
    if idx < 0:
        return {"agent": "already-frozen-or-absent"}
    entry = lst[idx]
    if entry.get("default"):
        return {"agent": "refuse-default", "hint": "the main agent is never frozen"}
    if dry:
        return {"agent": "dry-run", "would_freeze": entry.get("id")}
    frozen = _load(FROZEN_STORE, [])
    frozen = [f for f in frozen if f.get("id") != entry.get("id")]   # drop a stale copy first
    frozen.append(entry)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    shutil.copy2(OPENCLAW_CFG, OPENCLAW_CFG.with_name(OPENCLAW_CFG.name + f".bak-freeze-{ts}"))
    del lst[idx]
    _atomic_write(OPENCLAW_CFG, cfg)
    _atomic_write(FROZEN_STORE, frozen)
    return {"agent": "frozen", "id": entry.get("id"),
            "note": "unloaded from the gateway, agentDir intact; restart the gateway to apply"}


def thaw_agent(name, dry=False):
    """Return a frozen agent to agents.list. Reverses freeze_agent."""
    frozen = _load(FROZEN_STORE, [])
    fidx = next((i for i, f in enumerate(frozen)
                 if isinstance(f, dict) and (f.get("id") == name or f.get("name") == name)), -1)
    if fidx < 0:
        return {"agent": "not-frozen"}
    entry = frozen[fidx]
    if dry:
        return {"agent": "dry-run", "would_thaw": entry.get("id")}
    cfg = _load(OPENCLAW_CFG, {})
    lst = cfg.setdefault("agents", {}).setdefault("list", [])
    if _find_idx(lst, entry.get("id")) >= 0:                          # already active — just clear frozen
        del frozen[fidx]
        _atomic_write(FROZEN_STORE, frozen)
        return {"agent": "already-active", "id": entry.get("id")}
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    shutil.copy2(OPENCLAW_CFG, OPENCLAW_CFG.with_name(OPENCLAW_CFG.name + f".bak-thaw-{ts}"))
    lst.append(entry)
    del frozen[fidx]
    _atomic_write(OPENCLAW_CFG, cfg)
    _atomic_write(FROZEN_STORE, frozen)
    return {"agent": "thawed", "id": entry.get("id"),
            "note": "back in the gateway registry; restart the gateway to apply"}


def list_agents():
    cfg = _load(OPENCLAW_CFG, {})
    lst = cfg.get("agents", {}).get("list", [])
    active = [a.get("id") for a in lst if isinstance(a, dict) and not a.get("default")]
    frozen = [f.get("id") for f in _load(FROZEN_STORE, []) if isinstance(f, dict)]
    return {"active": active, "frozen": frozen,
            "default": next((a.get("id") for a in lst if isinstance(a, dict) and a.get("default")), None)}


def main():
    argv = sys.argv[1:]
    if not argv:
        print(json.dumps({"error": "usage: agent_registry.py freeze|thaw <name> [--dry] | list"})); return
    cmd = argv[0]
    dry = "--dry" in argv
    pos = [a for a in argv[1:] if not a.startswith("--")]
    if cmd == "list":
        print(json.dumps(list_agents(), ensure_ascii=False, indent=2)); return
    if cmd in ("freeze", "thaw") and pos:
        fn = freeze_agent if cmd == "freeze" else thaw_agent
        print(json.dumps(fn(pos[0], dry=dry), ensure_ascii=False, indent=2)); return
    print(json.dumps({"error": "usage: agent_registry.py freeze|thaw <name> [--dry] | list"}))


if __name__ == "__main__":
    main()
