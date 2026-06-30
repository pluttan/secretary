#!/usr/bin/env python3.13
# lastmile_tick.py — M6 daily check. Surfaces near-miss "stuck on the last mile" projects to
# the agent, which either presents the owner's OWN definition-of-done (if set) or gently asks
# for it, in Telegram. Once a day, gentle — this is support, not a nag.
#
# Gates (python, hard): gate=off → silent; mood=anxious → silent (not the time to push); no
# candidates → silent; once-a-day (.lastmile-last). The agent decides aptness (may answer QUIET).
# Self-onboarding: when the owner replies with a DoD, the persona records it via
# `project_cmd.py dod <name> <text>` (wired in the persona's TOOLS.md).
#
# Personal values (chat id, owner name) come from ../config.json (gitignored).
#   lastmile_tick.py / --force (skip gates) / --dry (print prompt, no LLM).
# stdlib only. Author: pluttan

import json
import re
import subprocess
import sys
from datetime import datetime
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
OWNER = _CFG.get("owner_name", "the owner")

OPENCLAW = str(HOME / ".nvm" / "versions" / "node" / "v24.14.1" / "bin" / "openclaw")
MSK = ZoneInfo("Europe/Moscow")
STATE = SECRETARY / "state" / "STATE.md"
DOD = SECRETARY / "state" / "dod.md"
LAST = SECRETARY / "state" / ".lastmile-last"
SPAWN = SECRETARY / "spawn"

NOISE = re.compile(r"^\s*(\x1b\[|\[plugins\]|.*registered:|hook runner|Gateway |Source:|Config:|Bind:|gateway connect|.*falling back|No reply)")
_BANNER = re.compile(r"(?i)(\btokens?\b|\bsession\b|\bmodel:|\bprovider\b|\busage\b|\bcache\b|\belapsed\b|^\s*[│├╭╰─▸●✔✓→•]+\s)")


def _is_noise(line):
    return bool(NOISE.match(line) or _BANNER.search(line))


def read_state():
    """gate='on|off', mood='ok|anxious' from STATE.md."""
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
        print(f"[m6] read_state: {type(e).__name__}: {e}", file=sys.stderr)
    return {"mood": mood, "gate": gate}


def detect():
    """Run the M6 detector (lastmile.py), return its candidate list."""
    try:
        r = subprocess.run(["python3.13", str(SPAWN / "lastmile.py")],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout or "{}").get("candidates", [])
    except Exception as e:
        print(f"[m6] detect: {type(e).__name__}: {e}", file=sys.stderr)
        return []


def dod_for(name):
    """The owner's DoD text for a project from dod.md, or None."""
    if not DOD.exists():
        return None
    m = re.search(rf"(?m)^## {re.escape(name)}\s*$\n(.*?)(?=^## |\Z)",
                  DOD.read_text(encoding="utf-8"), re.S)
    return (m.group(1).strip() or None) if m else None


def once_a_day(now):
    if not LAST.exists():
        return True
    try:
        return LAST.read_text().strip() != now.strftime("%Y-%m-%d")
    except Exception:
        return True


def build_prompt(now, cands):
    lines = []
    for c in cands:
        d = dod_for(c["name"])
        age = c.get("anchor_age_days")
        rs = "; ".join(c.get("reasons", []))
        head = c["name"] + (f" — застой ~{age}д" if age is not None else "")
        if d:
            lines.append(f"- {head}; {rs}\n    ЕГО DoD: {d}")
        else:
            lines.append(f"- {head}; {rs} — DoD НЕ задан")
    body = "\n".join(lines)
    return (
        "[LAST-MILE M6 — ты раз в день смотришь, не завис ли он близко к финишу. Это НЕ его сообщение, "
        "тебе не на что отвечать.]\n"
        f"Сейчас {now.strftime('%H:%M')}.\n\n"
        "Кандидаты «застрял на последней миле» (проект ACTIVE, но last-touch застыл / выпал из текущих "
        f"приоритетов, а до конца не доведён):\n{body}\n\n"
        "Возьми ОДИН — самый застрявший — и напиши ОДНО тёплое сообщение (или ровно 'QUIET'):\n"
        "- если DoD ЕСТЬ → предъяви ровно ЕГО: «ты сам сказал, по <проект> готово = <DoD>; что осталось "
        "ровно? дожмём?». по делу, без наезда.\n"
        "- если DoD НЕТ → мягко спроси: «<проект> висит ~<N>д — что для тебя значит \"довёл\" по нему? "
        "зафиксирую». (его ответ ты запишешь командой project_cmd dod.)\n"
        "Это ПОДДЕРЖКА, не душнёж — «ты как, не завис?», один проект за раз, не вали все. 'QUIET' если "
        "момент не тот или недавно про это говорили.\n"
        "Тон: тепло + ирония, lowercase, можно 🦊. Только текст сообщения, без преамбул и кавычек."
    )


def do_send(prompt, now):
    sess = f"lastmile-{int(now.timestamp())}"
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
        print(json.dumps({"ok": True, "sent": False, "skip": "mood anxious — не дожимаем"}, ensure_ascii=False)); return
    if not force and not once_a_day(now):
        print(json.dumps({"ok": True, "sent": False, "skip": "уже было сегодня"}, ensure_ascii=False)); return

    cands = detect()
    if not cands:
        print(json.dumps({"ok": True, "sent": False, "skip": "нет near-miss кандидатов"}, ensure_ascii=False)); return

    prompt = build_prompt(now, cands)
    if dry:
        print(prompt); return

    status, err, msg = do_send(prompt, now)
    if status in ("sent", "unknown"):
        LAST.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
    print(json.dumps({"ok": status != "failed", "sent": status == "sent", "status": status,
                      "candidates": len(cands), "msg": msg[:100], "err": err if status != "sent" else ""},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
