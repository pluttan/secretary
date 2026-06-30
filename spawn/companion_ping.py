#!/usr/bin/env python3.13
# companion_ping.py — M0: the agent judges window TITLES (no local model).
#
# The owner's mac ships focused-window titles to a host spool. Every 15 min this
# tick reads the recent titles + the `## now` priorities from STATE.md and asks the
# agent (DeepSeek persona) to judge: working on priorities, or drifting? It nudges
# on Telegram when the owner drifts, writes a friendly note if it has been quiet for
# a while, and stays silent otherwise. Nudger and companion are one tick.
#
# Hard gates (python): gate=off in STATE -> silent; no FRESH titles (mac off / owner
# away) -> silent; daily cap. No night gate: a mac on at night means the owner is up
# and almost certainly stuck, so the agent should write. Pacing/aptness are the
# agent's call (it sees gap, how many times it wrote today, mood).
#
# Personal values (telegram chat id, owner name) come from ../config.json (gitignored;
# template: config.example.json). Driven by shiki-companion.timer every 15 min.
#   companion_ping.py / --force (skip gates) / --dry (print prompt, no LLM).
# stdlib only. Author: pluttan

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

HOME = Path.home()
SECRETARY = HOME / "secretary"

# personal config (not in git) — chat id + owner name
_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
OWNER = _CFG.get("owner_name", "the owner")

OPENCLAW = str(HOME / ".nvm" / "versions" / "node" / "v24.14.1" / "bin" / "openclaw")
MSK_TZ = ZoneInfo("Europe/Moscow")
SPOOL = SECRETARY / "spool" / "inbox.ndjson"
STATE = SECRETARY / "state" / "STATE.md"
LAST = SECRETARY / "state" / ".companion-last"        # ts of our last message
LASTPROACTIVE = SECRETARY / "state" / ".last-proactive"  # text (agent reads it on a reply "into the void")
NUDGES = SECRETARY / "state" / ".m0-nudges"          # daily counter: "YYYY-MM-DD count"
# gateway session index — updatedAt of the owner DM (METAdata, message content not read):
# avoids writing a proactive right after a live conversation.
SESSIONS_INDEX = HOME / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"

WINDOW_MIN = 20           # titles window, min (a bit wider than the 15-min tick — overlap)
FRESH_MAX = 600           # sec: latest sample fresher than this -> mac on right now (else silent)
MAX_PER_DAY = 8           # daily cap on our messages (a guard against being a nag)
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Drop service noise from the agent stdout (banners/traces). The agent message is
# lowercase RU and contains none of these words; over-filter -> empty msg -> not sent
# (fail-safe, not garbage).
NOISE = re.compile(r"^\s*(\x1b\[|\[plugins\]|.*registered:|hook runner|Gateway |Source:|Config:|Bind:|gateway connect|.*falling back|No reply)")
_BANNER = re.compile(r"(?i)(\btokens?\b|\bsession\b|\bmodel:|\bprovider\b|\busage\b|\bcache\b|\belapsed\b|^\s*[│├╭╰─▸●✔✓→•]+\s)")


def _is_noise(line):
    return bool(NOISE.match(line) or _BANNER.search(line))


def msk():
    return datetime.now(MSK_TZ)


# ==========================
# ===  State reading     ===
# ==========================

def read_state():
    """Parse STATE.md: now=[priorities], mood='ok|anxious', gate='on|off'."""
    now, mood, gate, sec = [], "ok", "on", None
    try:
        for raw in STATE.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s.startswith("## "):
                sec = s[3:].strip().lower()
                continue
            if not s or s.startswith("<!--"):
                continue
            if sec == "now" and s.startswith("- "):
                now.append(s[2:].strip())
            elif sec == "mood":
                mood = s.lower()
            elif sec == "gate":
                gate = s.lower()
    except Exception as e:
        print(f"[m0] read_state: {type(e).__name__}: {e}", file=sys.stderr)
    return {"now": now, "mood": mood, "gate": gate}


def titles_window(window_min=WINDOW_MIN, fresh_max=FRESH_MAX):
    """Focused-window titles from the spool over a window. Returns (events, fresh):
    events = [(ts, app, title)] with consecutive duplicates (app,title) collapsed;
    fresh = is there a sample fresher than fresh_max sec (= mac is on right now)."""
    now = int(time.time())
    cutoff = now - window_min * 60
    rows = []
    try:
        with SPOOL.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 524288))   # tail ~512K (legacy lines with ocr_text are fat)
            chunk = f.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[m0] titles: {type(e).__name__}: {e}", file=sys.stderr)
        return [], False
    for ln in chunk.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        ts = int(d.get("ts", 0))
        if ts < cutoff:
            continue
        app = (d.get("active_app") or d.get("bundle_id") or "?").strip()
        title = (d.get("window_title") or "").strip()       # ocr_text NOT taken (privacy/weight)
        rows.append((ts, app, title))
    rows.sort()
    events = []
    for ts, app, title in rows:
        if events and events[-1][1] == app and events[-1][2] == title:
            continue        # same window in a row — collapse
        events.append((ts, app, title))
    fresh = bool(rows) and (now - rows[-1][0]) < fresh_max
    return events, fresh


# ==========================
# ===  Pacing (anti-spam) ==
# ==========================

def nudges_today(now):
    day = now.strftime("%Y-%m-%d")
    try:
        d, c = NUDGES.read_text().split()
        return int(c) if d == day else 0
    except Exception:
        return 0


def bump_nudges_today(now):
    NUDGES.write_text(f"{now.strftime('%Y-%m-%d')} {nudges_today(now) + 1}")


def last_dm_activity_ts():
    """epoch-sec of the last REAL owner DM (updatedAt from the session index, METAdata —
    content not read). Avoids writing a proactive right after a live conversation."""
    try:
        idx = json.loads(SESSIONS_INDEX.read_text())
    except Exception as e:
        print(f"[m0] last_dm: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    best = 0
    for k, v in (idx.items() if isinstance(idx, dict) else []):
        if not isinstance(v, dict) or CHAT_ID not in str(k) or "telegram" not in str(k).lower():
            continue
        try:
            best = max(best, int(v.get("updatedAt", 0)) / 1000.0)
        except Exception:
            pass
    return best


def gap_min(now):
    """Minutes since last activity — MIN of (our last proactive) and (live DM): a live
    conversation resets pacing the same as our own message."""
    nowts = now.timestamp()
    gaps = []
    if LAST.exists():
        try:
            gaps.append((now - datetime.fromisoformat(LAST.read_text().strip())).total_seconds() / 60)
        except Exception as e:
            print(f"[m0] gap/last: {type(e).__name__}: {e}", file=sys.stderr)
    dm = last_dm_activity_ts()
    if dm:
        gaps.append((nowts - dm) / 60)
    return min(gaps) if gaps else 1e9


def human_gap(gap):
    if gap >= 1e8:
        return "давно (не помню когда)"
    if gap < 120:
        return f"~{int(gap)} мин"
    if gap < 48 * 60:
        return f"~{int(gap // 60)} ч"
    return f"~{int(gap // (60 * 24))} дн"


# ==========================
# ===  Judge prompt      ===
# ==========================

def judge_prompt(now, events, st, gap, today):
    lines = []
    for ts, app, title in events[-25:]:
        t = datetime.fromtimestamp(ts, MSK_TZ).strftime("%H:%M")
        lines.append(f"{t} [{app}] {title}".rstrip())
    timeline = "\n".join(lines) if lines else "(заголовков нет)"
    prio = ", ".join(st["now"]) if st["now"] else "(не заданы)"
    mood_note = (f"\n{OWNER} сейчас тревожен (mood=anxious) — НЕ дави про залипание, только тепло "
                 "спроси «ты как», поддержи." if st["mood"] == "anxious" else "")
    return (
        "[ПРОАКТИВНЫЙ МОМЕНТ M0 — ты САМА смотришь, чем он занят, и решаешь: написать или "
        "промолчать. Это НЕ его сообщение, тебе не на что отвечать.]\n"
        f"Сейчас {now.strftime('%H:%M')}, {WEEKDAYS[now.weekday()]}. Твоё прошлое сообщение — "
        f"{human_gap(gap)} назад; сегодня ты уже написала {today} раз.\n\n"
        f"Приоритеты сейчас (## now): {prio}\n"
        f"Чем занят последние ~{WINDOW_MIN} мин (заголовки активных окон, сверху старее):\n{timeline}\n"
        f"{mood_note}\n\n"
        "Реши САМА, по заголовкам:\n"
        "- Работает по приоритету (код/терминал/доки/рабочий по теме ютуб-туториал) → ответь ровно "
        "'QUIET', не дёргай.\n"
        "- Залип/свернул не по делу (развлекательный ютуб, аниме, мемы, бесцельный скролл, соцсети "
        "не по работе) → поддушни КОРОТКО и по делу: назови, на что свернул, верни к приоритету. "
        "Без занудства, не одинаково каждый раз.\n"
        "- Тревожного нет, но давно молчали и момент норм → можешь по-человечески черкнуть "
        "(подколоть / как ты / подкинуть мысль). Иначе 'QUIET'.\n"
        "Не спамь: недавно писала или он явно в потоке работы — лучше 'QUIET'.\n\n"
        "Тон: тепло + ирония, lowercase, можно 🦊. ОДНО короткое живое сообщение ИЛИ ровно 'QUIET'. "
        "Только текст сообщения, без преамбул и кавычек."
    )


# ==========================
# ===  Compose + send    ===
# ==========================

def do_ping(prompt, now):
    """agent (captured) → .last-proactive → message send.
    Returns (status, err, msg): 'sent' | 'failed' | 'unknown'.
    'unknown' = send went out but the subprocess hung on read — treat as delivered, suppress dup."""
    sess = f"companion-{int(now.timestamp())}"   # fresh session — no QUIET history of past decisions
    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--agent", "main", "--session-id", sess, "--message", prompt],
            capture_output=True, text=True, timeout=150)
    except subprocess.TimeoutExpired:
        return "failed", "compose timeout (agent did not answer in 150s)", ""
    lines = [l for l in (r.stdout or "").splitlines() if l.strip() and not _is_noise(l)]
    msg = "\n".join(lines).strip()
    if not msg or msg.upper().strip(" .!\"'").startswith("QUIET"):
        return "failed", f"QUIET/empty (msg={msg[:40]!r})", ""
    LASTPROACTIVE.parent.mkdir(parents=True, exist_ok=True)
    LASTPROACTIVE.write_text(f"{now.strftime('%Y-%m-%d %H:%M')} MSK\n{msg}\n", encoding="utf-8")
    try:
        s = subprocess.run(
            [OPENCLAW, "message", "send", "--channel", "telegram", "--target", CHAT_ID, "--message", msg],
            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "unknown", "send timeout — maybe delivered, suppressing dup", msg
    return ("sent" if s.returncode == 0 else "failed"), (s.stderr or "")[-200:], msg


def main():
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv
    now = msk()
    st = read_state()

    if not force and st["gate"] == "off":
        print(json.dumps({"ok": True, "pinged": False, "skip": "gate off (veto)"}, ensure_ascii=False)); return

    events, fresh = titles_window()
    if not force and not fresh:
        print(json.dumps({"ok": True, "pinged": False, "skip": "no fresh titles (mac off/away)"}, ensure_ascii=False)); return

    today = nudges_today(now)
    if not force and today >= MAX_PER_DAY:
        print(json.dumps({"ok": True, "pinged": False, "skip": f"daily cap {MAX_PER_DAY}"}, ensure_ascii=False)); return

    gap = gap_min(now)
    prompt = judge_prompt(now, events, st, gap, today)
    if dry:
        print(prompt); return

    status, err, msg = do_ping(prompt, now)
    if status in ("sent", "unknown"):
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(now.isoformat(), encoding="utf-8")
        bump_nudges_today(now)
    print(json.dumps({"ok": status != "failed", "pinged": status == "sent", "status": status,
                      "events": len(events), "gap_min": int(gap) if gap < 1e8 else None,
                      "msg": msg[:100], "err": err if status != "sent" else ""}, ensure_ascii=False))


if __name__ == "__main__":
    main()
