#!/usr/bin/env python3.13
# comms.py — single reader over all conversations (M4 / §6.1 "ЕДИНЫЙ на все переписки"). Reads
# Telethon exports (~/tg-export/export/<chat>/messages.jsonl) and surfaces, without ever writing to
# third parties (no-egress; drafts go to Андрей only):
#   reply-debt  — dialogs where the last word isn't mine (я не ответил)
#   promises    — commitments mine/theirs ("пришлю/сделаю/до X" / "жду/пришлёшь")
#   deadlines   — dates/terms mentioned in chats
#   ghosts      — contacts gone quiet (cooled ties)
#   digest      — unified prioritized view
# lethal-trifecta break: this reader has NO egress and NO secrets; OTP is redacted on the way out.
# Extends to Gmail/Kwork when access lands. stdlib only. Author: pluttan

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redact

SECRETARY = Path.home() / "secretary"
DATA = SECRETARY / "state" / "comms_data"          # isolated WORK store (never the personal tg)
EXPORT = DATA
AUTHORS = DATA / "authors.json"

_CFG = {}
try:
    _CFG = json.loads((SECRETARY / "config.json").read_text())
except Exception:
    pass
MY_ID = int(_CFG.get("telegram_chat_id", 0) or 0)          # my telegram user id (= chat_id in DM)

_AUTHORS = {}
try:
    _AUTHORS = json.loads(AUTHORS.read_text())
except Exception:
    pass

# --- promise / deadline lexicons ---
_MINE = re.compile(r'(?i)\b(пришл[юё]|скину|отправлю|сделаю|доделаю|högь|высл|за[кк]ончу|'
                   r'напишу|перезвоню|обещаю|договорились|подготовлю|скинул бы|до завтра|к \w+)\b')
_THEIRS = re.compile(r'(?i)\b(жду|ждём|пришл[её]шь|скинешь|когда будет|сделаешь|дедлайн|'
                     r'сроки?|ждать|напомни|поторопи)\b')
# deadlines only in-context (preposition/keyword + date/day), not bare numbers/links/ids
_DEADLINE = re.compile(
    r'(?i)\b(?:до|к|дедлайн\w*|срок\w*|не позже|крайний срок|сдать|успе\w+|надо к)\s+'
    r'(?:\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?|завтра|послезавтра|сегодня|'
    r'понедельник\w*|вторник\w*|сред[уеы]\w*|четверг\w*|пятниц\w+|суббот\w+|воскресень\w+|'
    r'конц[ау]\s+\w+|начал[ау]\s+\w+|'
    r'\d{1,2}\s*(?:числа|янв\w*|фев\w*|март\w*|апрел\w*|ма[йя]\w*|июн\w*|июл\w*|авг\w*|'
    r'сент\w*|октяб\w*|нояб\w*|декаб\w*))'
    r'|\bна следующей неделе\b')

NOW = datetime.now(timezone.utc)
DEBT_DAYS = 1
DEBT_MAX = 45          # a debt older than this is a dead thread, not an unanswered message
GHOST_DAYS = 30
GHOST_MAX = 400        # cap ghosts to the last ~year, not ancient/bot chats


def _is_bot(name):
    return "bot" in (name or "").lower()


def _name(sid):
    return _AUTHORS.get(str(sid), f"id{sid}")


def _dt(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_dialogs():
    """Yield (dialog_name, [messages]) for every exported chat."""
    if not EXPORT.is_dir():
        return
    for d in sorted(EXPORT.iterdir()):
        f = d / "messages.jsonl"
        if not f.is_file():
            continue
        msgs = []
        for line in f.read_text(errors="ignore").splitlines():
            try:
                m = json.loads(line)
                if m.get("text"):
                    msgs.append(m)
            except Exception:
                pass
        if msgs:
            yield d.name, msgs


def _is_dm(msgs):
    """Heuristic: a DM has a small set of positive sender ids (not a -100… supergroup)."""
    senders = {m.get("sender_id") for m in msgs if m.get("sender_id")}
    return all(isinstance(s, int) and s > 0 for s in senders) and len(senders) <= 2


def reply_debt():
    out = []
    for name, msgs in load_dialogs():
        if not _is_dm(msgs):
            continue                                         # groups don't owe replies
        last = msgs[-1]
        if last.get("sender_id") != MY_ID:
            who = _name(last.get("sender_id"))
            if _is_bot(who):
                continue
            dt = _dt(last.get("date"))
            age = (NOW - dt).days if dt else None
            if age is not None and DEBT_DAYS <= age <= DEBT_MAX:
                out.append({"dialog": name, "who": who,
                            "age_days": age, "last": (last.get("text") or "")[:80]})
    out.sort(key=lambda x: -x["age_days"])
    return {"ok": True, "count": len(out), "debt": out}


def promises():
    mine, theirs = [], []
    for name, msgs in load_dialogs():
        for m in msgs:
            t = m.get("text") or ""
            dt = _dt(m.get("date"))
            when = dt.strftime("%d.%m") if dt else "?"
            if m.get("sender_id") == MY_ID and _MINE.search(t):
                mine.append({"dialog": name, "when": when, "text": t[:100]})
            elif m.get("sender_id") != MY_ID and _THEIRS.search(t):
                theirs.append({"dialog": name, "who": _name(m.get("sender_id")),
                               "when": when, "text": t[:100]})
    return {"ok": True, "mine": mine[-40:], "theirs": theirs[-40:]}


def deadlines():
    out = []
    for name, msgs in load_dialogs():
        dm = _is_dm(msgs)                                      # personal chats matter more than broadcasts
        for m in msgs:
            t = m.get("text") or ""
            hit = _DEADLINE.search(t)
            if hit:
                dt = _dt(m.get("date"))
                out.append({"dialog": name, "dm": dm, "when": dt.strftime("%d.%m") if dt else "?",
                            "match": hit.group(0), "text": t[:90]})
    out.sort(key=lambda x: (not x["dm"]))                      # personal first
    return {"ok": True, "count": len(out), "dm_count": sum(1 for x in out if x["dm"]),
            "deadlines": out[:50]}


def ghosts():
    last_seen = {}
    for name, msgs in load_dialogs():
        if not _is_dm(msgs):
            continue
        dt = _dt(msgs[-1].get("date"))
        if dt:
            last_seen[name] = (NOW - dt).days
    out = [{"dialog": n, "silent_days": d} for n, d in last_seen.items()
           if GHOST_DAYS <= d <= GHOST_MAX and not _is_bot(n)]
    out.sort(key=lambda x: x["silent_days"])
    return {"ok": True, "count": len(out), "ghosts": out}


def digest():
    debt = reply_debt()
    prom = promises()
    dl = deadlines()
    gh = ghosts()
    return {"ok": True,
            "reply_debt": debt["count"],
            "promises_mine": len(prom["mine"]),
            "promises_theirs": len(prom["theirs"]),
            "deadlines": dl["count"],
            "deadlines_personal": dl["dm_count"],
            "ghosts": gh["count"],
            "top_debt": debt["debt"][:5],
            "note": "Шики: сведи в приоритет — сперва долги по ответам (кому не ответил, дни), "
                    "потом мои обещания-хвосты, дедлайны из чатов, потом остывшие связи. "
                    "Наружу ничего не пиши — только черновик Андрею, он отправляет руками."}


def _redact_obj(o):
    return json.loads(redact.redact(json.dumps(o, ensure_ascii=False)))


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "digest"
    fn = {"debt": reply_debt, "promises": promises, "deadlines": deadlines,
          "ghosts": ghosts, "digest": digest}.get(cmd)
    if not fn:
        print(json.dumps({"error": "usage: debt|promises|deadlines|ghosts|digest"})); return
    print(json.dumps(_redact_obj(fn()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
