#!/usr/bin/env python3.13
# live_tick.py — daily-rhythm orchestrator. ONE connected conversation morning and evening,
# threading the other engines' agendas (planner, diary, last-mile, lifelog, ideas, reward) as
# TOPICS taken ONE AT A TIME — not three separate pings. Replaces the standalone last-mile /
# planner / journal timers; those become topics inside the live pass.
#
# It opens the pass with the FIRST topic only and writes the full agenda to state/.live-agenda;
# when the owner replies, the persona reads that file and walks to the next topic (TOOLS.md).
# morning vs evening picked by MSK hour (before 14:00 → morning).
#
# Gates: gate=off → silent; once-per-pass (.live-last holds "<date> <morning|evening>").
# Personal values from ../config.json (gitignored).
#   live_tick.py [morning|evening] / --force / --dry
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

OPENCLAW = str(HOME / ".nvm" / "versions" / "node" / "v24.14.1" / "bin" / "openclaw")
MSK = ZoneInfo("Europe/Moscow")
STATE = SECRETARY / "state" / "STATE.md"
LAST = SECRETARY / "state" / ".live-last"
AGENDA = SECRETARY / "state" / ".live-agenda"
SPAWN = SECRETARY / "spawn"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

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
        print(f"[live] read_state: {type(e).__name__}: {e}", file=sys.stderr)
    return {"mood": mood, "gate": gate}


def agenda_for(kind):
    """Run live.py morning|evening, return its aggregated JSON (or {})."""
    try:
        r = subprocess.run(["python3.13", str(SPAWN / "live.py"), kind],
                           capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout or "{}")
    except Exception as e:
        print(f"[live] agenda: {type(e).__name__}: {e}", file=sys.stderr)
        return {}


def pass_done(now, kind):
    tag = f"{now.strftime('%Y-%m-%d')} {kind}"
    try:
        return LAST.exists() and LAST.read_text().strip() == tag
    except Exception:
        return False


def build_prompt(now, kind, ag):
    wd = WEEKDAYS[now.weekday()]
    if kind == "morning":
        rp = ag.get("routine_pending") or []
        topics = [
            f"утренняя рутина не сделано: {', '.join(rp) or 'всё ✓'}",
            f"фокус дня: {ag.get('focus') or '—'}",
            f"просроченные якоря режима: {', '.join(ag.get('overdue_anchors') or []) or 'нет'}",
            f"всплывшие идеи: {', '.join(ag.get('due_ideas') or []) or 'нет'}",
        ]
        head = f"[LIVE — утренний расклад. ОДИН связный заход, главное по очереди. Это НЕ его сообщение.]"
        rule = ("Напиши бодрое утреннее ОТКРЫТИЕ + начни с ПЕРВОГО важного (фокус / last-mile). "
                "2-3 главного, не вали всё списком — остальное по ходу разговора.")
    else:
        need = ag.get("need_step") or []
        lm = ag.get("lastmile_candidates") or []
        rp = ag.get("routine_pending") or []
        streaks = ag.get("streaks") or {}
        sline = ", ".join(f"{k}:{v}д" for k, v in streaks.items()) or "—"
        topics = [
            f"не завис ли на последней миле: {', '.join(lm) or 'нет застрявших'} (предъяви его DoD или спроси)",
            f"вечерняя рутина не сделано: {', '.join(rp) or 'всё ✓'} (streak: {sline})",
            f"план на завтра (определить шаги по: {', '.join(need) or 'всё уже есть'})",
            "дневник за день (предложи записать впечатления — diary.py --save, '## заметка владельца')",
            "что довёл сегодня (отметь, поздравь — доведение = лекарство)",
        ]
        if ag.get("reward_earned"):
            topics.append("награда заработана (музыка-гейт разблокирован — по-доброму отметь)")
        head = "[LIVE — вечерний разбор. Это НЕ его сообщение, тебе не на что отвечать.]"
        rule = ("Напиши тёплое вечернее ОТКРЫТИЕ + ТОЛЬКО ПЕРВУЮ тему (план на завтра). "
                "НЕ вываливай все темы сразу. Когда он ответит — ты сама перейдёшь к следующей.")
    body = "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics))
    return (
        f"{head}\n"
        f"Сейчас {now.strftime('%H:%M')}, {wd} {'утро' if kind=='morning' else 'вечер'}.\n\n"
        f"Повестка ({'утра' if kind=='morning' else 'вечера'}) — собрана движками. "
        "ВЕДИ ПО ОДНОЙ теме, переходи к следующей КОГДА закроете текущую (по его ответу), "
        f"НЕ вываливай всё пачкой:\n{body}\n\n"
        f"{rule}\n"
        "'QUIET' если момент не тот. Тон: тепло, по-человечески, lowercase, можно 🦊. "
        "Только текст ПЕРВОГО сообщения, без преамбул и кавычек."
    )


def do_send(prompt, now, kind, ag):
    sess = f"live-{kind}-{int(now.timestamp())}"
    try:
        r = subprocess.run([OPENCLAW, "agent", "--agent", "main", "--session-id", sess, "--message", prompt],
                           capture_output=True, text=True, timeout=150)
    except subprocess.TimeoutExpired:
        return "failed", "compose timeout", ""
    lines = [l for l in (r.stdout or "").splitlines() if l.strip() and not _is_noise(l)]
    msg = "\n".join(lines).strip()
    if not msg or msg.upper().strip(" .!\"'").startswith("QUIET"):
        return "failed", f"QUIET/empty (msg={msg[:40]!r})", ""
    # persist the agenda so the persona can walk topics on the owner's replies
    AGENDA.parent.mkdir(parents=True, exist_ok=True)
    AGENDA.write_text(json.dumps({"ts": now.strftime("%Y-%m-%d %H:%M"), "kind": kind, "agenda": ag,
                                  "opened_with": msg}, ensure_ascii=False, indent=2), encoding="utf-8")
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
    kind = next((a for a in sys.argv[1:] if a in ("morning", "evening")), None) or \
        ("morning" if now.hour < 14 else "evening")
    st = read_state()

    if not force and st["gate"] == "off":
        print(json.dumps({"ok": True, "sent": False, "skip": "gate off (veto)"}, ensure_ascii=False)); return
    if not force and pass_done(now, kind):
        print(json.dumps({"ok": True, "sent": False, "skip": f"{kind} уже был сегодня"}, ensure_ascii=False)); return

    ag = agenda_for(kind)
    prompt = build_prompt(now, kind, ag)
    if dry:
        print(prompt); return

    status, err, msg = do_send(prompt, now, kind, ag)
    if status in ("sent", "unknown"):
        LAST.write_text(f"{now.strftime('%Y-%m-%d')} {kind}", encoding="utf-8")
    print(json.dumps({"ok": status != "failed", "sent": status == "sent", "status": status,
                      "kind": kind, "msg": msg[:100], "err": err if status != "sent" else ""},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
