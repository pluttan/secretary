#!/usr/bin/env python3.13
# planner_tick.py — evening planner. Builds the "plan for tomorrow" draft (planner.py --save),
# then the agent asks the owner what's on for tomorrow per active project, pulls undefined next
# steps (feeds money-path), and suggests freeze for projects with no ideas left. Once a day, ~22:00.
#
# Gates (python, hard): gate=off → silent; mood=anxious → silent (not the time to plan); once-a-day
# (.planner-last). The agent decides aptness (may answer QUIET). The owner's real plan is captured
# in the dialogue and the `## правки владельца` section of plan-<date>.md (persona's TOOLS.md).
#
# Personal values come from ../config.json (gitignored).
#   planner_tick.py / --force / --dry
# stdlib only. Author: pluttan

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"
_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))

OPENCLAW = str(HOME / ".nvm" / "versions" / "node" / "v24.14.1" / "bin" / "openclaw")
MSK = ZoneInfo("Europe/Moscow")
STATE = SECRETARY / "state" / "STATE.md"
STATE_DIR = SECRETARY / "state"
LAST = SECRETARY / "state" / ".planner-last"
SPAWN = SECRETARY / "spawn"

NOISE = re.compile(r"^\s*(\x1b\[|\[plugins\]|.*registered:|hook runner|Gateway |Source:|Config:|Bind:|gateway connect|.*falling back|No reply)")
_BANNER = re.compile(r"(?i)(\btokens?\b|\bsession\b|\bmodel:|\bprovider\b|\busage\b|\bcache\b|\belapsed\b|^\s*[│├╭╰─▸●✔✓→•]+\s)")


def _is_noise(line):
    return bool(NOISE.match(line) or _BANNER.search(line))


def read_state():
    mood, gate, sec = "ok", "on", None
    try:
        for raw in STATE.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s.startswith("## "):
                sec = s[3:].strip().lower(); continue
            if not s or s.startswith("<!--"):
                continue
            if sec == "mood":
                mood = s.lower()
            elif sec == "gate":
                gate = s.lower()
    except Exception as e:
        print(f"[planner] read_state: {type(e).__name__}: {e}", file=sys.stderr)
    return {"mood": mood, "gate": gate}


def build_draft():
    """Run planner.py --save, return (draft_text, signal). Falls back gracefully."""
    try:
        r = subprocess.run(["python3.13", str(SPAWN / "planner.py"), "--save"],
                           capture_output=True, text=True, timeout=30)
        sig = json.loads(r.stdout or "{}")
        draft = ""
        saved = sig.get("saved")
        if saved and Path(saved).exists():
            draft = Path(saved).read_text(encoding="utf-8")
        return draft, sig
    except Exception as e:
        print(f"[planner] draft: {type(e).__name__}: {e}", file=sys.stderr)
        return "", {}


def once_a_day(now):
    if not LAST.exists():
        return True
    try:
        return LAST.read_text().strip() != now.strftime("%Y-%m-%d")
    except Exception:
        return True


def build_prompt(now, draft, sig):
    wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][now.weekday()]
    need = sig.get("need_step", [])
    no_active = not sig.get("active")
    extra = ""
    if need:
        extra += f"\nУ этих проектов ближайший шаг НЕ определён — вытяни конкретный шаг: {', '.join(need)}."
    if no_active:
        extra += "\nАктивных проектов нет — спроси, что берём завтра (или ничего — тоже ок)."
    return (
        "[PLANNER — вечер, ты собираешь с владельца план на завтра. Это НЕ его сообщение, тебе не на "
        "что отвечать.]\n"
        f"Сейчас {now.strftime('%H:%M')}, {wd} вечер.\n\n"
        f"Черновик на завтра (активные проекты + ближайший шаг из money-path; «ШАГ НЕ ОПРЕДЕЛЁН» = дыра):\n"
        f"{draft or '(черновик пуст)'}\n"
        f"{extra}\n\n"
        "Напиши ОДНО тёплое вечернее сообщение (или ровно 'QUIET', если поздно / не момент):\n"
        "- спроси «что на завтра?» по проектам в фокусе — коротко, не простынёй-списком.\n"
        "- по «ШАГ НЕ ОПРЕДЕЛЁН» — вытяни конкретный ближайший шаг (это наполняет money-path).\n"
        "- если по какому-то проекту идей на завтра нет совсем → мягко предложи freeze (нет идей → "
        "заморозка, это ОК, не провал).\n"
        "Тон: тепло, по-вечернему, lowercase, можно 🦊. Один заход, не вали все проекты сразу. "
        "Только текст сообщения, без преамбул и кавычек."
    )


def do_send(prompt, now):
    sess = f"planner-{int(now.timestamp())}"
    try:
        r = subprocess.run([OPENCLAW, "agent", "--agent", "main", "--session-id", sess, "--message", prompt],
                           capture_output=True, text=True, timeout=150)
    except subprocess.TimeoutExpired:
        return "failed", "compose timeout", ""
    lines = [l for l in (r.stdout or "").splitlines() if l.strip() and not _is_noise(l)]
    msg = "\n".join(lines).strip()
    if not msg or msg.upper().strip(" .!\"'").startswith("QUIET"):
        return "failed", f"QUIET/empty (msg={msg[:40]!r})", ""
    try:
        s = subprocess.run([OPENCLAW, "message", "send", "--channel", "telegram", "--target", CHAT_ID, "--message", msg],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "unknown", "send timeout — maybe delivered", msg
    return ("sent" if s.returncode == 0 else "failed"), (s.stderr or "")[-200:], msg


def main():
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv
    now = datetime.now(MSK)
    st = read_state()

    if not force and st["gate"] == "off":
        print(json.dumps({"ok": True, "sent": False, "skip": "gate off (veto)"}, ensure_ascii=False)); return
    if not force and st["mood"] == "anxious":
        print(json.dumps({"ok": True, "sent": False, "skip": "mood anxious — не грузим планами"}, ensure_ascii=False)); return
    if not force and not once_a_day(now):
        print(json.dumps({"ok": True, "sent": False, "skip": "уже было сегодня"}, ensure_ascii=False)); return

    draft, sig = build_draft()
    prompt = build_prompt(now, draft, sig)
    if dry:
        print(prompt); return

    status, err, msg = do_send(prompt, now)
    if status in ("sent", "unknown"):
        LAST.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
    print(json.dumps({"ok": status != "failed", "sent": status == "sent", "status": status,
                      "active": sig.get("active", []), "need_step": sig.get("need_step", []),
                      "msg": msg[:100], "err": err if status != "sent" else ""}, ensure_ascii=False))


if __name__ == "__main__":
    main()
