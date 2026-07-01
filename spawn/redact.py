#!/usr/bin/env python3.13
# redact.py — redaction filter for the M3 defensive perimeter (PLAN DoD: "redaction-фильтр не
# пускает секреты в контекст/логи/телегу"). Two layers:
#   (1) EXACT — every value found in the secrets dir is masked wherever it appears in the text;
#   (2) STRUCTURAL — token/key/OTP shapes are masked even for secrets we don't hold (bot tokens,
#       API keys, github/slack/aws keys, PEM blocks, OTP-in-context).
# Applied to outgoing Telegram text/caption and to anything logged. stdlib only. Author: pluttan

import json
import re
import sys
from pathlib import Path

SECRETARY = Path.home() / "secretary"
_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

MASK = "[вырезано]"

_PATTERNS = [
    re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{30,}\b'),                 # telegram bot token
    re.compile(r'\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b'),             # openai/anthropic-style key
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),                 # github token
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),              # slack
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),                          # aws access key id
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S),
    re.compile(r'\b[A-Fa-f0-9]{64,}\b'),                          # long hex (keys/hashes)
    re.compile(r'\b[A-Za-z0-9+/]{50,}={0,2}\b'),                  # long base64 blob (keys/cookies)
]
_OTP = re.compile(r'(?i)\b(код|code|otp|пароль|password|pin|одноразов\w*)\b(\D{0,30})(\d{4,8})\b')


def _secret_values():
    vals = set()
    try:
        for f in SECRETS.iterdir():
            if not f.is_file():
                continue
            for line in f.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line and " " not in line.split("=", 1)[0]:    # KEY=VALUE → take VALUE
                    line = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(line) >= 8:                                       # skip short/empty tokens
                    vals.add(line)
    except Exception:
        pass
    return sorted(vals, key=len, reverse=True)                          # longest first


def redact(text):
    if not text:
        return text
    s = str(text)
    for v in _secret_values():
        if v in s:
            s = s.replace(v, MASK)
    for pat in _PATTERNS:
        s = pat.sub(MASK, s)
    s = _OTP.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", s)
    return s


def _self_test():
    cases = [
        ("токен 1234567890:ABCdefGHIjklMNOpqrstUVwxyz1234567890", True),
        ("ключ sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123", True),
        ("твой код подтверждения 845213 не пересылай", True),
        ("обычное сообщение про задачу, число 42 тут ок", False),
        ("github ghp_ABCdefGHIjklMNOpqrstuvwxyz0123456789", True),
    ]
    ok = True
    for text, should_mask in cases:
        r = redact(text)
        masked = MASK in r
        status = "OK" if masked == should_mask else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  [{status}] mask={masked} want={should_mask} :: {r}")
    print("self-test:", "PASS" if ok else "FAIL")
    return ok


def main():
    a = sys.argv[1:]
    if a and a[0] == "--self-test":
        sys.exit(0 if _self_test() else 1)
    txt = " ".join(a) if a else sys.stdin.read()
    print(redact(txt))


if __name__ == "__main__":
    main()
