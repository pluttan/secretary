#!/usr/bin/env python3.13
# secret.py — centralized secret access for the M3 perimeter (PLAN DoD: "критичные секреты в age,
# не в .env; модуль запрашивает секрет в момент использования"). Reads encrypted <name>.age via
# age (identity ~/.config/age/secretary.key), falling back to legacy plaintext <name>. Secrets are
# fetched at point of use — never put in env/argv/LLM-context.
#
#   secret.py get <name>          — print a secret (age-first, plaintext-fallback)
#   secret.py encrypt <name...>   — plaintext <name> → <name>.age (verifies, keeps plaintext)
#   secret.py check               — decrypt every *.age, confirm it matches plaintext if present
#   secret.py rm-plain <name...>  — delete plaintext after its .age verifies (at-rest hardening)
# stdlib + age binary. Author: pluttan

import json
import subprocess
import sys
from pathlib import Path

SECRETARY = Path.home() / "secretary"
_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
SECRETS = Path(_CFG.get("secrets_dir", "~/.secrets")).expanduser()

AGE = Path.home() / ".local" / "bin" / "age"
AGE_KEYGEN = Path.home() / ".local" / "bin" / "age-keygen"
KEY = Path.home() / ".config" / "age" / "secretary.key"


def _pubkey():
    r = subprocess.run([str(AGE_KEYGEN), "-y", str(KEY)], capture_output=True, text=True, timeout=15)
    return r.stdout.strip()


def get(name):
    """Fetch a secret value. age-first (<name>.age), then legacy plaintext (<name>)."""
    enc = SECRETS / f"{name}.age"
    if enc.exists() and KEY.exists() and AGE.exists():
        r = subprocess.run([str(AGE), "-d", "-i", str(KEY), str(enc)],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return r.stdout.rstrip("\n")
    plain = SECRETS / name
    if plain.exists():
        return plain.read_text().strip()
    raise KeyError(f"secret not found: {name}")


def encrypt(name):
    plain = SECRETS / name
    if not plain.exists():
        return {"ok": False, "name": name, "error": "no_plaintext"}
    pk = _pubkey()
    if not pk:
        return {"ok": False, "name": name, "error": "no_pubkey"}
    enc = SECRETS / f"{name}.age"
    r = subprocess.run([str(AGE), "-r", pk, "-o", str(enc)],
                       input=plain.read_bytes(), capture_output=True, timeout=15)
    if r.returncode != 0:
        return {"ok": False, "name": name, "error": r.stderr.decode()[:120]}
    enc.chmod(0o600)
    match = get(name) == plain.read_text().strip()      # verify round-trip
    return {"ok": match, "name": name, "encrypted": str(enc), "verified": match}


def check():
    out = []
    for enc in sorted(SECRETS.glob("*.age")):
        name = enc.stem
        r = subprocess.run([str(AGE), "-d", "-i", str(KEY), str(enc)],
                           capture_output=True, text=True, timeout=15)
        dec_ok = r.returncode == 0
        plain = SECRETS / name
        matches = (plain.read_text().strip() == r.stdout.rstrip("\n")) if (dec_ok and plain.exists()) else None
        out.append({"name": name, "decrypts": dec_ok, "matches_plaintext": matches})
    return {"ok": all(x["decrypts"] for x in out), "secrets": out}


def rm_plain(name):
    enc = SECRETS / f"{name}.age"
    plain = SECRETS / name
    if not enc.exists():
        return {"ok": False, "name": name, "error": "no_age"}
    if get(name) != (plain.read_text().strip() if plain.exists() else get(name)):
        return {"ok": False, "name": name, "error": "verify_failed"}
    if plain.exists():
        plain.unlink()
    return {"ok": True, "name": name, "plaintext_removed": True}


def main():
    a = sys.argv[1:]
    if a and a[0] == "get" and len(a) >= 2:
        print(get(a[1])); return
    if a and a[0] == "encrypt" and len(a) >= 2:
        print(json.dumps([encrypt(n) for n in a[1:]], ensure_ascii=False)); return
    if a and a[0] == "check":
        print(json.dumps(check(), ensure_ascii=False, indent=2)); return
    if a and a[0] == "rm-plain" and len(a) >= 2:
        print(json.dumps([rm_plain(n) for n in a[1:]], ensure_ascii=False)); return
    print(json.dumps({"error": "usage: get <name>|encrypt <name...>|check|rm-plain <name...>"}))


if __name__ == "__main__":
    main()
