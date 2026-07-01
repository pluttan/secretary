#!/usr/bin/env python3.13
# golden_hours.py — energy/productivity profile by hour of day (M2 DoD: "гистограмма золотых часов
# построена из данных"). Fuses three presence/activity signals into a 24h profile:
#   git-commit hours (strong work signal) · zsh-history command hours (activity) ·
#   tracker heartbeat hours (active presence, idle<120s).
# Each source is normalized to its own share, so a high-volume source (tracker ~30s beats) doesn't
# drown a sparse one (commits). Golden hours = the peak band → the windows to put pressure into.
# Text histogram (mosh-safe block chars). stdlib only. Author: pluttan

import json
import statistics
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reports

MSK = ZoneInfo("Europe/Moscow")
SECRETARY = Path.home() / "secretary"
SPOOL = SECRETARY / "spool" / "inbox.ndjson"
ZSH = Path.home() / ".zsh_history"
DAYS = 60
BARS = "▁▂▃▄▅▆▇█"


def _msk_hour(epoch):
    return datetime.fromtimestamp(int(epoch), MSK).hour


def git_hours():
    hrs = [0] * 24
    for cand in reports.discover():
        host = "mac" if cand.startswith("mac:") else "local"
        path = cand[4:] if host == "mac" else cand
        cmd = ["git", "-C", path, "log", f"--since={DAYS} days ago",
               "--date=format-local:%H", "--format=%cd"]
        if host == "mac":
            cmd = ["ssh", "-o", "ConnectTimeout=8", "macair", " ".join(cmd)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
            for l in out.splitlines():
                l = l.strip()
                if l.isdigit():
                    hrs[int(l) % 24] += 1
        except Exception:
            pass
    return hrs


def zsh_hours():
    hrs = [0] * 24
    cutoff = (datetime.now(MSK) - timedelta(days=DAYS)).timestamp()
    try:
        for line in ZSH.read_text(errors="ignore").splitlines():
            if line.startswith(": "):
                try:
                    epoch = int(line.split(":", 2)[1])
                    if epoch >= cutoff:
                        hrs[_msk_hour(epoch)] += 1
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return hrs


def tracker_hours():
    hrs = [0] * 24
    cutoff = (datetime.now(MSK) - timedelta(days=DAYS)).timestamp()
    try:
        for line in SPOOL.read_text(errors="ignore").splitlines():
            try:
                d = json.loads(line)
                ts = int(d.get("ts", 0))
                if ts >= cutoff and d.get("idle_seconds", 999) < 120:      # active presence
                    hrs[_msk_hour(ts)] += 1
            except Exception:
                pass
    except Exception:
        pass
    return hrs


def _norm(hrs):
    tot = sum(hrs) or 1
    return [h / tot for h in hrs]


def profile():
    srcs = {"git": git_hours(), "zsh": zsh_hours(), "tracker": tracker_hours()}
    combined = [0.0] * 24
    used = 0
    for hrs in srcs.values():
        if sum(hrs) == 0:
            continue
        used += 1
        n = _norm(hrs)
        for h in range(24):
            combined[h] += n[h]
    if used:
        combined = [c / used for c in combined]
    return combined, {k: sum(v) for k, v in srcs.items()}


def _golden_ranges(combined):
    mx = max(combined) or 1
    thr = max(statistics.mean(combined) * 1.25, mx * 0.5)
    hot = [h for h in range(24) if combined[h] >= thr and combined[h] > 0]
    ranges, run = [], []
    for h in hot:
        if run and h == run[-1] + 1:
            run.append(h)
        else:
            if run:
                ranges.append(run)
            run = [h]
    if run:
        ranges.append(run)
    return [f"{r[0]:02d}-{r[-1] + 1:02d}" for r in ranges]


def render():
    combined, totals = profile()
    if sum(totals.values()) == 0:
        return "золотых часов пока не собрать — нет данных (git/zsh/трекер пусты)"
    mx = max(combined) or 1
    row = "".join(BARS[min(int(c / mx * 7), 7)] for c in combined)
    ruler = "0  2  4  6  8  10 12 14 16 18 20 22"
    ticks = "".join("|" if h % 2 == 0 else " " for h in range(24))
    golden = _golden_ranges(combined)
    src = ", ".join(f"{k}:{v}" for k, v in totals.items() if v)
    return (f"золотые часы (MSK, {DAYS}д · {src}):\n"
            f"{ticks}\n{row}\n{ruler}\n"
            f"пик: {', '.join(golden) if golden else '—'}")


def main():
    a = sys.argv[1:]
    if a and a[0] == "--json":
        combined, totals = profile()
        print(json.dumps({"profile": combined, "golden": _golden_ranges(combined),
                          "sources": totals}, ensure_ascii=False)); return
    print(render())


if __name__ == "__main__":
    main()
