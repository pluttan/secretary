#!/usr/bin/env python3.13
# guard.py — M3 infra watchdog: disk-forecast + backup-age + mac source-of-truth reachability.
# Closes the materialised risks the plan names (trendy died quiet, backup went stale, disk 89%).
# Runs on a timer; alerts are deduped (one ping per condition until it clears + cooldown) and
# pushed to telegram via de-german, redacted. stdlib only. Author: pluttan
#
#   guard.py check        — run all checks, alert on threshold (driven by timer)
#   guard.py status       — JSON snapshot, no alerts

import glob
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact

SECRETARY = Path.home() / "secretary"
STATE = SECRETARY / "state" / "guard.json"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

DISK_PATHS = _CFG.get("guard_disk_paths", ["/", "/data"])
BACKUP_GLOBS = _CFG.get("guard_backup_globs", ["~/pr-backup-*", "/data/**/pr-backup*", "/data/backup*"])
MAC_HOST = _CFG.get("guard_mac_host", "macair")
DISK_PCT_ALERT = 88
DISK_FORECAST_DAYS = 14
BACKUP_MAX_AGE_DAYS = 10
MAC_FAILS_ALERT = 2          # consecutive failed checks before alerting
COOLDOWN_H = 12              # don't repeat the same active alert within this window


def _now():
    return datetime.now(timezone.utc)


def _load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"disk_hist": {}, "mac_fails": 0, "alerts": {}}


def _save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


# ---------- checks ----------
def check_disk(s):
    out = []
    for p in DISK_PATHS:
        try:
            u = shutil.disk_usage(p)
        except Exception:
            continue
        pct = round(u.used / u.total * 100, 1)
        hist = s["disk_hist"].setdefault(p, [])
        hist.append([_now().isoformat(), pct])
        s["disk_hist"][p] = hist[-60:]                     # keep last ~60 samples
        forecast = None
        if len(hist) >= 3:
            t0 = datetime.fromisoformat(hist[0][0]); p0 = hist[0][1]
            days = max((_now() - t0).total_seconds() / 86400, 0.01)
            rate = (pct - p0) / days                       # %/day
            if rate > 0.01:
                forecast = round((95 - pct) / rate, 1)
        out.append({"path": p, "pct": pct, "forecast_days": forecast})
    return out


def check_backup():
    newest = None
    for g in BACKUP_GLOBS:
        for path in glob.glob(str(Path(g).expanduser()), recursive=True):
            try:
                m = Path(path).stat().st_mtime
                if newest is None or m > newest[0]:
                    newest = (m, path)
            except Exception:
                continue
    if not newest:
        return {"found": False, "age_days": None, "path": None}
    age = (_now().timestamp() - newest[0]) / 86400
    return {"found": True, "age_days": round(age, 1), "path": newest[1]}


def check_mac(s):
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", MAC_HOST, "echo ok"],
                           capture_output=True, text=True, timeout=20)
        alive = r.stdout.strip() == "ok"
    except Exception:
        alive = False
    s["mac_fails"] = 0 if alive else s.get("mac_fails", 0) + 1
    return {"alive": alive, "fails": s["mac_fails"]}


# ---------- alerting ----------
def _tg(text):
    try:
        token = (SECRETS / "telegram-bot-token").read_text().strip()
    except Exception:
        return None
    from urllib.parse import quote
    cfg = [f'url = "https://api.telegram.org/bot{token}/sendMessage"',
           f'data = "chat_id={CHAT_ID}"',
           f'data = "text={quote(redact.redact(text), safe="")}"']
    try:
        subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "de-german",
                        "curl -s --max-time 20 -K -"],
                       input="\n".join(cfg) + "\n", capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"[guard] tg: {type(e).__name__}: {e}", file=sys.stderr)


def _maybe_alert(s, key, active, text):
    """Fire `text` only when condition flips to active or cooldown elapsed; clear when inactive."""
    rec = s["alerts"].get(key)
    if not active:
        if rec:
            s["alerts"].pop(key, None)
        return False
    if rec:
        last = datetime.fromisoformat(rec)
        if _now() - last < timedelta(hours=COOLDOWN_H):
            return False
    s["alerts"][key] = _now().isoformat()
    _tg(text)
    return True


def tick(alerting=True):
    s = _load()
    disk = check_disk(s)
    backup = check_backup()
    mac = check_mac(s)
    fired = []
    if alerting:
        for d in disk:
            full = d["pct"] >= DISK_PCT_ALERT
            soon = d["forecast_days"] is not None and d["forecast_days"] <= DISK_FORECAST_DAYS
            if _maybe_alert(s, f"disk:{d['path']}", full or soon,
                            f"⚠ диск {d['path']}: {d['pct']}% занято"
                            + (f", прогноз до 95% ~{d['forecast_days']} дн" if soon else "")):
                fired.append(f"disk:{d['path']}")
        old = backup["found"] and backup["age_days"] is not None and backup["age_days"] > BACKUP_MAX_AGE_DAYS
        if _maybe_alert(s, "backup", old or not backup["found"],
                        (f"⚠ бэкап протух: {backup['age_days']} дн ({backup['path']})" if backup["found"]
                         else "⚠ бэкап не найден ни по одному из путей")):
            fired.append("backup")
        if _maybe_alert(s, "mac", mac["fails"] >= MAC_FAILS_ALERT,
                        f"⚠ мак ({MAC_HOST}) недоступен — {mac['fails']} проверок подряд (source-of-truth кода)"):
            fired.append("mac")
    _save(s)
    return {"ok": True, "disk": disk, "backup": backup, "mac": mac, "fired": fired}


def main():
    a = sys.argv[1:]
    if a and a[0] == "status":
        print(json.dumps(tick(alerting=False), ensure_ascii=False, indent=2)); return
    print(json.dumps(tick(alerting=True), ensure_ascii=False))


if __name__ == "__main__":
    main()
