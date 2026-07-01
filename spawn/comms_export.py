#!/usr/bin/env python3.13
# comms_export.py — incremental auto-export of Telegram into ~/tg-export/export via Telethon, so
# comms.py always reads fresh data without any manual step. Per-dialog last-id checkpoint → pulls
# only new messages, appends JSONL in the format comms.py expects. First touch of a dialog grabs a
# recent window (FIRST_LIMIT) instead of its whole history; after that it's cheap deltas. Session is
# the cached export_session (owner already logged in). Timer-driven. Author: pluttan

import asyncio
import json
import sys
from pathlib import Path

TGEXPORT = Path.home() / "tg-export"
sys.path.insert(0, str(TGEXPORT))
import config                                                  # api_id / api_hash / phone
from telethon import TelegramClient

EXPORT = TGEXPORT / "export"
SESSION = str(TGEXPORT / "export_session")
AUTHORS = TGEXPORT / "authors.json"
CKPT = Path.home() / "secretary" / "state" / "comms_export.json"
FIRST_LIMIT = 300                                              # recent window on first touch of a dialog


def _slug(s):
    keep = " -_@."
    out = "".join(c if (c.isalnum() or c in keep) else "_" for c in str(s))
    return out.strip("_ ") or "chat"


def _load_ckpt():
    try:
        return json.loads(CKPT.read_text())
    except Exception:
        return {}


def _save_ckpt(d):
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    CKPT.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def _load_authors():
    try:
        return json.loads(AUTHORS.read_text())
    except Exception:
        return {}


async def export_all():
    client = TelegramClient(SESSION, config.api_id, config.api_hash)
    await client.start(phone=getattr(config, "phone", None))
    me = await client.get_me()
    ckpt = _load_ckpt()
    authors = _load_authors()
    stats = []
    async for dialog in client.iter_dialogs():
        key = str(dialog.id)
        min_id = ckpt.get(key, 0)
        first = min_id == 0
        name = _slug(dialog.name or key)
        out_dir = EXPORT / name
        out_dir.mkdir(parents=True, exist_ok=True)
        rows, last = [], min_id
        try:
            kwargs = {"limit": FIRST_LIMIT} if first else {"min_id": min_id, "reverse": True}
            async for msg in client.iter_messages(dialog.entity, **kwargs):
                sid = msg.sender_id
                if sid and str(sid) not in authors:
                    try:
                        s = await msg.get_sender()
                        nm = getattr(s, "first_name", None) or getattr(s, "title", None) or ""
                        un = getattr(s, "username", None)
                        authors[str(sid)] = (nm + (f" (@{un})" if un else "")).strip() or f"id{sid}"
                    except Exception:
                        pass
                rows.append({"id": msg.id,
                             "date": msg.date.isoformat() if msg.date else None,
                             "sender_id": sid,
                             "reply_to": getattr(msg.reply_to, "reply_to_msg_id", None) if msg.reply_to else None,
                             "fwd_from": bool(msg.fwd_from),
                             "text": msg.text or None})
                last = max(last, msg.id)
        except Exception as e:
            print(f"[cexp] {name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if not rows:
            continue
        rows.sort(key=lambda r: r["id"])                       # first-run window comes newest-first
        with open(out_dir / "messages.jsonl", "a", encoding="utf-8") as w:
            for r in rows:
                w.write(json.dumps(r, ensure_ascii=False) + "\n")
        ckpt[key] = last
        stats.append({"dialog": name, "new": len(rows), "first": first})
    _save_ckpt(ckpt)
    AUTHORS.write_text(json.dumps(authors, ensure_ascii=False))
    await client.disconnect()
    return {"me": me.id, "stats": stats}


def main():
    res = asyncio.run(export_all())
    stats = res["stats"]
    print(json.dumps({"ok": True, "me_id": res["me"],
                      "dialogs_updated": len(stats),
                      "new_messages": sum(s["new"] for s in stats),
                      "detail": stats[:25]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
