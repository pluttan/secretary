#!/usr/bin/env python3.13
# reports_img.py — year activity heatmap, the PICTURE form of the "граф зелёных квадратиков" in
# the reports aspect (FINAL-PLAN §6). Replaces yougileTgBot's node/puppeteer epg with pure Pillow:
# aggregates commit dates across all owner repos over 365 days into a 53-week × 7-day github-style
# grid (Tokyo Night palette), sent to telegram via de-german sendPhoto.
#
#   reports_img.py year            — build + send the year heatmap
#   reports_img.py year --dry      — just build /tmp, print stats (no send)
# Author: pluttan

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reports
import redact

SECRETARY = Path.home() / "secretary"
_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
CHAT_ID = str(_CFG.get("telegram_chat_id", ""))
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

# Tokyo Night palette: bg + 5 green activity levels (empty → bright)
BG = (26, 27, 38)
LEVELS = [(40, 42, 58), (31, 58, 40), (45, 90, 61), (65, 166, 111), (115, 218, 202)]
TEXT = (122, 132, 175)
MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
CELL, PAD, LEFT, TOP = 13, 3, 32, 24


def _font(size=9):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def build_year_counter():
    counter = {}
    for cand in reports.discover():
        host = "mac" if cand.startswith("mac:") else "local"
        path = cand[4:] if host == "mac" else cand
        for d in (reports.git_dates(host, path, days=365) or []):
            counter[d] = counter.get(d, 0) + 1
    return counter


def _level(n):
    # thresholds tuned for an active dev with bulk/squash days, so the gradient stays readable
    return 0 if n == 0 else 1 if n <= 3 else 2 if n <= 9 else 3 if n <= 29 else 4


def draw_year(counter, out_path):
    today = date.today()
    start = today - timedelta(days=364)
    start -= timedelta(days=start.weekday())           # align to Monday
    weeks = ((today - start).days // 7) + 1
    W = LEFT + weeks * (CELL + PAD) + PAD
    H = TOP + 7 * (CELL + PAD) + 18
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f = _font(9)
    for i, lbl in [(0, 'пн'), (2, 'ср'), (4, 'пт')]:
        d.text((4, TOP + i * (CELL + PAD) + 1), lbl, fill=TEXT, font=f)
    last_month, total, active = None, 0, 0
    for w in range(weeks):
        for dow in range(7):
            cd = start + timedelta(days=w * 7 + dow)
            if cd > today:
                continue
            n = counter.get(cd.strftime("%Y-%m-%d"), 0)
            total += n
            active += 1 if n else 0
            x = LEFT + w * (CELL + PAD)
            y = TOP + dow * (CELL + PAD)
            d.rectangle([x, y, x + CELL, y + CELL], fill=LEVELS[_level(n)])
            if dow == 0 and cd.day <= 7 and cd.month != last_month:
                d.text((x, 8), MONTHS[cd.month - 1], fill=TEXT, font=f)
                last_month = cd.month
    d.text((LEFT, H - 14), f"{total} коммитов · {active} активных дней · {today.strftime('%d.%m.%Y')}",
           fill=TEXT, font=f)
    img.save(out_path)
    return {"commits": total, "active_days": active, "weeks": weeks, "path": str(out_path)}


def _send_photo(path, caption):
    try:
        token = (SECRETS / "telegram-bot-token").read_text().strip()
    except Exception:
        return None
    if subprocess.run(["scp", "-q", "-o", "ConnectTimeout=10", str(path), "de-german:/tmp/sy.png"],
                      timeout=40).returncode != 0:
        return None
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    cmd = (f"curl -s --max-time 30 -F chat_id={CHAT_ID} "
           f"--form-string caption={json.dumps(redact.redact(caption), ensure_ascii=False)} "
           f"-F photo=@/tmp/sy.png '{url}'; rm -f /tmp/sy.png")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "de-german", cmd],
                           capture_output=True, text=True, timeout=70)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        print(f"[rimg] sendPhoto: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def send_year(dry=False):
    out = Path("/tmp/secretary-year.png")
    stats = draw_year(build_year_counter(), out)
    if dry:
        return {"ok": True, "dry": True, **stats}
    cap = f"год активности · {stats['commits']} коммитов, {stats['active_days']} активных дней"
    res = _send_photo(out, cap)
    return {"ok": bool(res and res.get("ok")), **stats}


def main():
    a = sys.argv[1:]
    dry = "--dry" in a
    print(json.dumps(send_year(dry=dry), ensure_ascii=False))


if __name__ == "__main__":
    main()
