#!/usr/bin/env python3.13
# secretaryd — M0 brain ("m0-poke").
#
# Reads heartbeats appended by the mac tracker into the spool, judges each
# screen ("work on a priority" vs "leak / distraction") with a LOCAL LLM
# (ollama m0judge), accumulates how long the user has been stuck, and nudges
# them on Telegram when a funnel crosses a threshold. The only outward channel
# is a Telegram DM to the owner himself, routed through de-german.
#
# stdlib only (urllib, subprocess, json) — no venv needed for M0.
# Author: pluttan

import json
import time
import subprocess
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# --- Twenty portfolio register (best-effort, never blocks M0) ---
# The systemd --user service may start with cwd != secretaryd/, so make the
# sibling module importable by absolute dir before importing it.
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import twenty_client as tw          # sibling module in secretaryd/
    TWENTY_OK = True
except Exception as e:
    tw = None
    TWENTY_OK = False
    # fact only, no secrets: the module just did not load
    print(f"[twenty] disabled: {e}", flush=True)

# the owner lives in MSK; pcomp runs in UTC. All human-facing time (quiet hours,
# daily rollover, journal stamps) must be MSK, not the server clock.
MSK = ZoneInfo("Europe/Moscow")
def now_msk():
    return datetime.now(MSK)

# ============================
# ===  Paths & constants   ===
# ============================
BASE     = Path.home() / "secretary"
SPOOL    = BASE / "spool" / "inbox.ndjson"
STATE_MD = BASE / "state" / "STATE.md"
OFFSET_F = BASE / "state" / ".spool_offset"
RUNTIME  = BASE / "state" / ".runtime.json"
JOURNAL  = BASE / "journal"
# secrets dir from config.json (gitignored; template config.example.json)
try:
    SECRETS = Path(json.loads((BASE / "config.json").read_text()).get("secrets_dir", "~/.secrets")).expanduser()
except Exception:
    SECRETS = Path("~/.secrets").expanduser()

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL      = "m0judge"

# ============================
# ===  Config (thresholds) ===
# ============================
CFG = {
    "funnel_warn_sec":   600,   # залип (leak подряд) дольше -> первый пинок (10 мин)
    "funnel_escalate_sec": 1500, # дольше -> жёстче (25 мин)
    "nudge_cooldown_sec": 600,  # не пинать чаще, чем раз в 10 мин
    "max_nudges_per_day": 5,    # потолок пинков/день
    "quiet_start_hour":  2,     # ночное окно тишины M0 (MSK) = 02:00-07:00 — глубокая ночь.
    "quiet_end_hour":    7,     # Намеренно УЖЕ companion-ночи (00:00-08:00): companion — это
                               # болтовня (тихо всю ночь), а M0 ловит залипание и может пнуть
                               # в 00-02/07-08, если владелец реально доскроллился (F13: by-design).
    "judge_timeout_sec": 60,
    "poll_interval_sec": 5,
    # --- F6/F7: устойчивость воронки к шуму судьи и разрывам трекера ---
    "work_reset_streak":       2,    # сколько подряд work, чтобы обнулить leak_since (антишум F6)
    "heartbeat_stale_sec":     600,  # хартбит старше -> реплей бэклога/простой, не судим и не пинаем (F7)
    "heartbeat_gap_break_sec": 300,  # разрыв между хартбитами больше -> разрыв сессии, воронку с нуля (F7)
    # --- Twenty portfolio register ---
    "twenty_touch_sec":       180,   # bump an active track's last-touch at most once / 3 min
    "wip_signal_cooldown_sec": 3600, # WIP dispersion signal at most once an hour
}

# ============================
# ===  STATE.md parsing    ===
# ============================
def read_state():
    """Возвращает (now_list, mood, gate_on)."""
    now, mood, gate = [], "ok", True
    if not STATE_MD.exists():
        return now, mood, gate
    section = None
    for raw in STATE_MD.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if not line or line.startswith("<!--") or line.startswith("#"):
            continue
        if section == "now" and line.startswith("-"):
            now.append(line.lstrip("- ").strip())
        elif section == "mood":
            mood = line.lower()
        elif section == "gate":
            gate = line.lower() != "off"
    return now, mood, gate

# ============================
# ===  Local LLM judge     ===
# ============================
def judge(ocr_text, title, now):
    """Спросить m0judge: work или leak. Возвращает (label, reason)."""
    anchors = ", ".join(now) if now else "(приоритеты не заданы)"
    prompt = (
        "Определи по тексту с экрана, занят ли человек делом по своим приоритетам "
        "или отвлёкся (прокрастинирует).\n"
        f"Приоритеты человека: {anchors}.\n\n"
        "Правило: код, терминал, документация, обучение ПО ТЕМЕ приоритетов = work. "
        "Развлечения не по делу (аниме, сериалы, развлекательный ютуб, соцсети, мемы) = leak.\n\n"
        "Примеры:\n"
        'Экран: "VS Code main.py def handler import os" -> {"label":"work","reason":"пишет код"}\n'
        'Экран: "YouTube One Piece Episode 1089 аниме онлайн" -> {"label":"leak","reason":"смотрит аниме"}\n'
        'Экран: "YouTube tutorial Ollama API Python part 3" -> {"label":"work","reason":"обучение по теме"}\n'
        'Экран: "Twitter лента мемы коты подписаться" -> {"label":"leak","reason":"залип в соцсетях"}\n'
        'Экран: "Terminal git commit cargo build npm run" -> {"label":"work","reason":"работа в терминале"}\n\n'
        f"Заголовок окна: {title!r}\n"
        f"Текст с экрана:\n{ocr_text[:3000]}\n\n"
        'Ответь СТРОГО одной строкой JSON: '
        '{"label":"work" или "leak","reason":"кратко по-русски"}.'
    )
    body = json.dumps({
        "model": MODEL, "stream": False, "format": "json", "prompt": prompt,
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=CFG["judge_timeout_sec"]) as r:
            resp = json.load(r)
        verdict = json.loads(resp.get("response", "{}"))
        label = verdict.get("label", "").lower()
        if label not in ("work", "leak"):
            return "neutral", "модель не дала чёткий ответ"
        return label, verdict.get("reason", "")
    except Exception as e:
        return "neutral", f"judge error: {e}"

# ============================
# ===  Notify via openclaw  ===
# ============================
# secretaryd НЕ формулирует пинок и НЕ шлёт в телегу сам. Он отдаёт СОБЫТИЕ-метку
# openclaw-агенту — тот формулирует, помнит контекст и ведёт диалог.
# ГРАНИЦА ПРИВАТНОСТИ (решение владельца 2026-06-29, аудит F1/F3/F122 — принято as-is):
#   • СЫРОЙ экран (ocr_text, window_title) судится ЛОКАЛЬНО (ollama m0judge) и машину НЕ покидает.
#   • КОРОТКИЙ вердикт (work/leak + одна фраза reason) + active_app + имена приоритетов уходят
#     openclaw-агенту main, который крутится на ОБЛАЧНОМ deepseek — владелец это сознательно принял
#     (мозг ассистента и так deepseek). Это НЕ «без текста экрана», как было раньше написано.
#   • Twenty-путь — строже: туда уходит ТОЛЬКО структура (имя/стадия/таймстамп), без reason/app
#     (см. NEVER-список ниже) — портфель это persistent-стор третьей стороны, не диалог.
OPENCLAW    = str(Path.home() / ".nvm" / "versions" / "node" / "v24.14.1" / "bin" / "openclaw")
# owner's telegram chat id from config.json (gitignored; template config.example.json)
try:
    OWNER_CHAT = str(json.loads((BASE / "config.json").read_text()).get("telegram_chat_id", ""))
except Exception:
    OWNER_CHAT = ""

def notify(event):
    """Скормить событие openclaw-агенту; он формулирует и доставляет в telegram.
    F5: проверяем returncode. Раньше падение/таймаут openclaw глотался, нудж терялся
    молча, но воронка считала его доставленным (бюджет тратился, повтор блокировался
    cooldown/cap). Теперь возвращаем False при rc!=0 — вызывающий не тратит бюджет."""
    try:
        r = subprocess.run(
            [OPENCLAW, "agent", "--agent", "main", "--to", OWNER_CHAT,
             "--channel", "telegram", "--deliver", "--message", event],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"[notify] openclaw rc={r.returncode}: {(r.stderr or '')[-200:]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[notify] {type(e).__name__}: {e}", flush=True)
        return False

# ============================
# ===  Twenty portfolio     ===
# ============================
# All Twenty writes are best-effort: the client uses a short (4s) timeout, every
# call is wrapped here and any exception is logged + swallowed. M0 (nudges) is
# more critical than the portfolio store. To bound M0 latency, the per-heartbeat
# work is capped to AT MOST ONE write (sync promotes one name per tick); the WIP
# list runs only once the snapshot is fully synced. CONSTITUTION: only STRUCTURE
# goes to Twenty (name from STATE ## now, stage, anchor = last-touch ISO, flags)
# — NEVER ocr_text, window_title, active_app, judge.reason, or heartbeat content.

def track_name_for(anchor):
    """Map a STATE ## now token to a Track name 1:1.

    INVARIANT: a Track 'name' written to Twenty MUST originate ONLY from a
    STATE ## now token (human-curated by the owner via /now). NEVER pass ocr_text,
    window_title, active_app, or any heartbeat field here — that would leak
    screen content into the store. This is the single source of track names.
    """
    return anchor.strip()

def twenty_try(fn, *a, **k):
    """Run any Twenty call best-effort: swallow + log errors, never raise."""
    if not TWENTY_OK:
        return None
    try:
        return fn(*a, **k)
    except Exception as e:
        print(f"[twenty] {getattr(fn, '__name__', 'call')} failed: {e}", flush=True)
        return None

def _twenty_touch_active(rt, now, hb):
    """On a work verdict for the active priority: bump last-touch + ACTIVE.
    Throttled per-name via runtime so we don't hit Twenty every heartbeat.
    Only name/stage/timestamp leave — never reason/ocr_text/title/app."""
    if not TWENTY_OK or not now:
        return
    name = track_name_for(now[0])           # now[0] = current focus (as NUDGE-warn uses)
    nowts = hb.get("ts", int(time.time()))
    touched = rt.setdefault("twenty_last_touch", {})
    last = touched.get(name, 0)
    if nowts - last < CFG["twenty_touch_sec"]:
        return                              # throttle: at most once / N min
    ok = twenty_try(tw.upsert_track, name, stage="ACTIVE", last_touch=nowts)
    if ok is not None:
        touched[name] = nowts               # advance throttle only on success

def _twenty_sync_now(rt, now):
    """Promote STATE ## now tokens to stage=ACTIVE, AT MOST ONE per heartbeat,
    and only while the snapshot differs from what we've already synced. Tracks
    that fell out of ## now are NOT touched (dropping a priority != freezing a
    project; only the owner demotes by hand). Bounding to one write/tick keeps M0
    stall <= ~one client timeout even when Twenty is hung."""
    if not TWENTY_OK:
        return
    cur = [track_name_for(x) for x in now]
    synced = rt.get("twenty_last_now", [])
    if cur == synced:
        return                              # snapshot fully synced -> no Twenty call
    pending = [nm for nm in cur if nm not in synced]
    if not pending:
        rt["twenty_last_now"] = cur         # only drops fell off -> record, no write
        return
    nm = pending[0]                         # ONE write this tick
    ok = twenty_try(tw.upsert_track, nm, stage="ACTIVE")
    if ok is None:
        return                              # Twenty down -> retry next heartbeat
    keep = set(synced) | {nm}
    rt["twenty_last_now"] = [x for x in cur if x in keep]
    if rt["twenty_last_now"] == cur:        # snapshot now complete -> WIP check (1 list)
        _twenty_wip_check(rt, now)

def _twenty_wip_check(rt, now):
    """Dispersion signal: alarm only when ACTIVE tracks EXCEED the number of
    declared priorities in STATE ## now (stale ACTIVE piling up) — NOT on the
    sanctioned 1-3 priorities. Shares the daily poke budget + cooldown so its
    safeties truly mirror the nudges (gate/mood/quiet/cap/cooldown/anti-flap)."""
    if not TWENTY_OK:
        return
    active = twenty_try(tw.list_tracks, stage="ACTIVE")
    if active is None:                      # Twenty unreachable -> stay silent
        return
    n = len(active)
    declared = max(1, len(now))             # STATE allows 1-3 -> that many ACTIVE is fine
    if n <= declared:
        rt["wip_last_count"] = n            # within declared focus -> reset anti-flap
        return
    nowts = int(time.time())
    _, mood, gate = read_state()
    if not gate or mood == "anxious" or in_quiet_hours():
        return
    if rt.get("nudges_today", 0) >= CFG["max_nudges_per_day"]:
        return                              # WIP shares the daily poke cap
    if nowts - rt.get("wip_last_signal", 0) < CFG["wip_signal_cooldown_sec"]:
        return
    if nowts - rt.get("last_nudge", 0) < CFG["nudge_cooldown_sec"]:
        return                              # F16: WIP уважает общий cooldown пинков (не дубль подряд)
    if n == rt.get("wip_last_count", 0):    # anti-flap: already signaled this N
        return
    names = ", ".join(sorted(t.get("name", "?") for t in active))
    if not notify(f"[событие от детектора] WIP-разлёт: активных треков {n}, "
           f"а приоритетов в фокусе {declared} ({names}). "
           f"Похоже, проекты копятся в ACTIVE — что заморозить? Скажи мягко, как наблюдатель."):
        return                              # F5: доставка упала — бюджет не тратим
    rt["wip_last_signal"] = nowts
    rt["wip_last_count"] = n
    rt["last_nudge"] = nowts                # consume the shared budget...
    rt["nudges_today"] = rt.get("nudges_today", 0) + 1   # ...like a real poke

def portfolio_digest(send=True):
    """Portfolio mirror: track slice -> short summary -> openclaw -> telegram.
    Called by an EXTERNAL trigger (cron/command) ONLY — NEVER from the heartbeat
    loop (notify() can block ~120s on the openclaw subprocess). Best-effort.
    Only name/stage/last-touch — portfolio facts, never screen text."""
    if not TWENTY_OK:
        return None
    tracks = twenty_try(tw.list_tracks)
    if not tracks:
        return None
    def fmt_age(iso):
        if not iso:
            return "?"
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            days = (now_msk() - dt.astimezone(MSK)).days
            return "сегодня" if days == 0 else f"{days}д назад"
        except Exception:
            return "?"
    order = ["ACTIVE", "BACKLOG", "FROZEN", "SHIPPED", "KILLED"]
    bystage = {}
    for t in tracks:
        bystage.setdefault(t.get("stage", "?"), []).append(t)
    lines = []
    for st in order:
        for t in bystage.get(st, []):
            lines.append(f"{t.get('name', '?')} — {st}, last-touch {fmt_age(t.get('anchor'))}")
    summary = "Портфель проектов:\n" + "\n".join(lines)
    if send:
        notify(f"[событие: дайджест портфеля] {summary} "
               f"Передай владельцу кратко, человеческим языком.")
    return summary

# ============================
# ===  Runtime state       ===
# ============================
def _atomic_write(path, text):
    """F11: атомарная запись (tmp в той же папке + fsync + os.replace). Обрыв/краш
    (pcomp виснет раз в 4-6 дней) не оставит усечённый JSON/offset — иначе load_runtime
    сбрасывал состояние в дефолт, а битый .spool_offset → полный реплей 3 MiB спула."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def load_runtime():
    if RUNTIME.exists():
        try:
            return json.loads(RUNTIME.read_text())
        except Exception:
            pass
    return {"leak_since": 0, "last_nudge": 0, "nudges_today": 0,
            "nudge_day": "", "leak_level": 0,
            "work_streak": 0, "last_hb_ts": 0,   # F6 антишум-дебаунс / F7 разрыв сессии
            # --- Twenty throttling/sync state ---
            "twenty_last_touch": {},   # {track_name: unix_ts of last successful touch}
            "twenty_last_now": [],     # names already synced to ACTIVE (incremental, change detection)
            "wip_last_signal": 0,      # unix_ts of last WIP signal
            "wip_last_count": 0}       # ACTIVE count at the last signal (anti-flap)

def save_runtime(rt):
    _atomic_write(RUNTIME, json.dumps(rt))

# ============================
# ===  Spool reader        ===
# ============================
def read_new_heartbeats():
    if not SPOOL.exists():
        return []
    size = SPOOL.stat().st_size
    if not OFFSET_F.exists():
        # F7: первый запуск -> начинаем с КОНЦА, бэклог не реплеим. Старые ts иначе
        # мгновенно превышают порог эскалации и пинают «из прошлого» (stuck=49599s).
        _atomic_write(OFFSET_F, str(size))
        return []
    try:
        off = int(OFFSET_F.read_text().strip())
    except Exception:
        off = size               # битый offset -> к концу (skip-to-tail), НЕ 0 (без реплея 3 MiB)
    if off > size:               # spool rotated/truncated
        off = 0
    with SPOOL.open("rb") as f:
        f.seek(off)
        data = f.read()
    # F10: не глотаем оборванную последнюю строку (частичная дозапись трекера) —
    # двигаем offset только за последний полный '\n', хвост дочитаем в следующий проход.
    out, consumed = [], off
    if data:
        last_nl = data.rfind(b"\n")
        complete = data[:last_nl + 1] if last_nl != -1 else b""
        if last_nl != -1:
            consumed = off + last_nl + 1
        for raw in complete.splitlines():
            raw = raw.strip()
            if raw:
                try:
                    out.append(json.loads(raw))
                except Exception:
                    pass
    _atomic_write(OFFSET_F, str(consumed))
    return out

# ============================
# ===  Core decision       ===
# ============================
def in_quiet_hours():
    h = now_msk().hour                 # MSK, not the UTC server clock
    qs, qe = CFG["quiet_start_hour"], CFG["quiet_end_hour"]
    # F12: окно может переходить через полночь (qs>qe, напр. 22..7). При qs<qe — обычный
    # диапазон. Раньше qs<=h<qe при qs>qe давало ВСЕГДА False -> тишина молча отключалась.
    return (qs <= h < qe) if qs < qe else (h >= qs or h < qe)

def handle(hb, rt):
    """Обработать один heartbeat, при необходимости пнуть."""
    now, mood, gate = read_state()
    today = now_msk().strftime("%Y-%m-%d")
    if rt.get("nudge_day") != today:
        rt["nudge_day"] = today
        rt["nudges_today"] = 0

    # STATE ## now -> stage=ACTIVE (one write/tick, only while snapshot differs)
    # + WIP check on completion. Independent of the screen; runs before the
    # no-screenshot early return. Internally capped to <=1 Twenty write/tick.
    _twenty_sync_now(rt, now)

    # F7: ts хартбита = настенное время мака. Ловим разрывы и устаревшие метки ДО суда.
    nowts = hb.get("ts", int(time.time()))
    prev_ts = rt.get("last_hb_ts", 0)
    rt["last_hb_ts"] = nowts
    # Разрыв сессии (мак спал / владельца не было) -> воронку с нуля, не пинаем «из прошлого».
    if prev_ts and nowts - prev_ts > CFG["heartbeat_gap_break_sec"]:
        rt["leak_since"] = 0
        rt["leak_level"] = 0
        rt["work_streak"] = 0
    # Устаревший хартбит (реплей бэклога после простоя) -> не судим и не пинаем сейчас.
    if int(time.time()) - nowts > CFG["heartbeat_stale_sec"]:
        return None

    # Без скрина/текста не судим — метаданные одни ничего не доказывают.
    if not hb.get("had_screenshot") or not hb.get("ocr_text"):
        return None

    if not now:                         # нет приоритетов -> не из чего пинать
        return None

    label, reason = judge(hb["ocr_text"], hb.get("window_title", ""), now)

    if label == "work":
        # F6: не разоружать воронку ОДНИМ шумным work. Маленькая модель m0judge нередко
        # выдаёт work посреди залипа (label=work, reason=«отвлёкся на ютуб») — раньше это
        # обнуляло 10/25-мин таймер. Сбрасываем leak_since только после N подряд work.
        rt["work_streak"] = rt.get("work_streak", 0) + 1
        if rt["work_streak"] >= CFG["work_reset_streak"]:
            rt["leak_since"] = 0
            rt["leak_level"] = 0
        _twenty_touch_active(rt, now, hb)   # best-effort, throttled per-name inside
        return ("work", reason)

    if label != "leak":
        rt["work_streak"] = 0
        # F9: neutral из-за ОШИБКИ судьи (ollama упала/таймаут) не должен копить воронку —
        # иначе при восстановлении stuck учтёт «слепое» время и ложно эскалирует.
        # Замораживаем leak_since, сдвигая его на длительность слепого окна.
        if reason.startswith("judge error") and rt.get("leak_since") and prev_ts:
            rt["leak_since"] += max(0, nowts - prev_ts)
        return ("neutral", reason)

    # leak: засекаем, сколько длится (nowts уже вычислен выше для F7)
    rt["work_streak"] = 0
    if not rt.get("leak_since"):
        rt["leak_since"] = nowts
    stuck = nowts - rt["leak_since"]

    # предохранители
    if not gate:                 return ("leak-gateoff", reason)
    if mood == "anxious":        return ("leak-anxious", reason)
    if in_quiet_hours():         return ("leak-quiet", reason)
    if rt["nudges_today"] >= CFG["max_nudges_per_day"]:
        return ("leak-capped", reason)
    if nowts - rt.get("last_nudge", 0) < CFG["nudge_cooldown_sec"]:
        return ("leak-cooldown", reason)

    app = hb.get("active_app", "?")
    if stuck >= CFG["funnel_escalate_sec"] and rt.get("leak_level", 0) < 2:
        mins = stuck // 60
        if not notify(f"[событие от детектора] владелец залип в «{app}» уже {mins} мин, "
               f"приоритеты простаивают: {', '.join(now)}. Судья определил: {reason}. "
               f"Это эскалация (давно завис) — дожми вернуться к делу, но без перебора."):
            return ("leak-deliver-failed", reason)   # F5: доставка упала — бюджет/leak_level не трогаем, повтор на следующем тике
        rt["leak_level"] = 2
        rt["last_nudge"] = nowts
        rt["nudges_today"] += 1
        return ("NUDGE-escalate", reason)

    if stuck >= CFG["funnel_warn_sec"] and rt.get("leak_level", 0) < 1:
        mins = stuck // 60
        if not notify(f"[событие от детектора] владелец завис в «{app}» ~{mins} мин при активном "
               f"приоритете «{now[0]}», к делу не вернулся. Судья: {reason}. "
               f"Среагируй как наблюдатель — мягко спроси, к делу это или соскользнул."):
            return ("leak-deliver-failed", reason)   # F5: доставка упала — не тратим бюджет
        rt["leak_level"] = 1
        rt["last_nudge"] = nowts
        rt["nudges_today"] += 1
        return ("NUDGE-warn", reason)

    return ("leak-watching", reason)

# ============================
# ===  Main loop           ===
# ============================
def main():
    rt = load_runtime()
    while True:
        for hb in read_new_heartbeats():
            res = handle(hb, rt)
            if res:
                tag, reason = res
                # F8: stuck осмыслен только при активной воронке (leak_since!=0). Раньше для
                # work/neutral печаталось stuck=ts-0 (мусор ~1.78e9) и засоряло журнал + metki().
                if rt.get("leak_since"):
                    dur = f"stuck={max(0, hb.get('ts', 0) - rt['leak_since'])}s "
                else:
                    dur = ""
                print(f"[{now_msk():%H:%M:%S}] {tag}: app={hb.get('active_app')} {dur}"
                      f"reason={reason}", flush=True)
            save_runtime(rt)
        time.sleep(CFG["poll_interval_sec"])

if __name__ == "__main__":
    main()
