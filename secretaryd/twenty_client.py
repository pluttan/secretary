#!/usr/bin/env python3.13
# twenty_client.py — Twenty (self-host) GraphQL client for the secretary's
# portfolio register. Lives next to secretaryd.py and is imported as
# `import twenty_client as tw`.
#
# Twenty here is a LOCAL (127.0.0.1) store/showcase of the owner's pet-projects.
# Per the constitution we write ONLY structural portfolio facts into it:
# project name, stage, last-touch timestamp, flags. NEVER ocr_text /
# window_title / heartbeat content / any screen text — that stays local to
# secretaryd and never reaches a store.
#
# stdlib only (urllib, json, os, pathlib, time, datetime) — python3.13, no venv.
# Endpoint is hard-wired to the core GraphQL API. REST (/rest) hangs on timeout
# and /metadata is schema-only — neither is touched here.
#
# Secrets (Bearer api key) are read from <secrets_dir>/twenty.env at runtime
# (secrets_dir is set in config.json) and are NEVER logged, printed, or embedded
# in exception text.
#
# Author: pluttan

import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ============================
# ===  Constants           ===
# ============================
GQL_URL   = "http://127.0.0.1:3010/graphql"          # core GraphQL ONLY
# secrets dir from config.json (gitignored; template config.example.json)
try:
    _SECRETS_DIR = Path(json.loads((Path.home() / "secretary" / "config.json").read_text()).get("secrets_dir", "~/.secrets")).expanduser()
except Exception:
    _SECRETS_DIR = Path("~/.secrets").expanduser()
SECRETS_F = _SECRETS_DIR / "twenty.env"
# Short timeout: M0 (nudges) matters far more than the portfolio store. This is
# the per-request network deadline, NOT the per-tick bound: one upsert is up to
# TWO sequential round-trips (find_by_name + create/update), so a single write
# can stall up to 2*TIMEOUT, and a snapshot tick additionally runs a wip-check
# list (+1 round-trip). secretaryd caps writes to one/tick, but the real worst
# case is a small multiple of TIMEOUT — keep TIMEOUT low so it stays inside M0.
TIMEOUT   = 4                                         # seconds (was 8 — review: M0 latency)
VALID_STAGES = {"BACKLOG", "ACTIVE", "FROZEN", "KILLED", "SHIPPED"}

_CREDS = None                                         # lazy creds cache (read env once)


# ============================
# ===  Error type          ===
# ============================
class TwentyError(Exception):
    """Single error type the caller catches and swallows (best-effort writes).

    Its message is built from the fact of failure (status/type/short gql msg)
    only — it MUST NOT contain the api key or request headers.
    """
    pass


# ============================
# ===  Credentials         ===
# ============================
def load_creds():
    """Parse twenty.env into a dict and cache it. Values are NEVER logged."""
    global _CREDS
    if _CREDS is not None:
        return _CREDS
    if not SECRETS_F.exists():
        raise TwentyError("twenty.env not found")
    d = {}
    for raw in SECRETS_F.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        d[k.strip()] = v.strip().strip('"').strip("'")
    if not d.get("TWENTY_API_KEY"):
        raise TwentyError("TWENTY_API_KEY missing")
    _CREDS = d
    return d


# ============================
# ===  Transport (private) ===
# ============================
def _gql(query, variables=None):
    """The single network I/O point. All public functions go through here.

    On any failure raises TwentyError with a SAFE short message (no api key,
    no request body, no echoed HTTP error body).
    """
    global _CREDS
    creds = load_creds()
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        GQL_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            # Bearer assembled at runtime, never logged.
            "Authorization": "Bearer " + creds["TWENTY_API_KEY"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        # body NOT included: it can reflect the request back. Do not widen this
        # to e.read()/e.reason — keep it status-only (privacy review).
        if e.code in (401, 403):
            # auth rejected -> the cached api key is likely stale (twenty.env was
            # rotated). Drop the cache so the NEXT call re-reads the env file;
            # otherwise a long-lived secretaryd keeps sending the dead key and
            # every write 401s silently until restart. Fact-only stderr signal —
            # status code only, never the key or headers.
            _CREDS = None
            print(f"[twenty] WARN auth rejected (http {e.code}) — dropped cached "
                  f"creds, re-reading twenty.env next call", file=sys.stderr, flush=True)
        raise TwentyError(f"http {e.code}")
    except Exception as e:
        # timeout / connection refused / etc. -> caught by the caller upstream
        raise TwentyError(f"net {type(e).__name__}")
    if payload.get("errors"):
        # Inputs are structure-only (name/stage/ts/currency), so a reflected
        # value here is portfolio data, not screen text. Still truncated.
        msgs = "; ".join(str(e.get("message", "?"))[:120] for e in payload["errors"])
        raise TwentyError("gql: " + msgs)
    return payload.get("data") or {}


# ============================
# ===  Time helper         ===
# ============================
def _iso(dt_or_epoch):
    """Render a datetime / epoch into ISO-8601 with millis and Z.

    Accepts an aware datetime, an int/float epoch, or None-free callers.
    A naive datetime is assumed to be UTC (rather than silently shifting by
    the server locale) — callers should pass time.time() or an aware dt.
    """
    if isinstance(dt_or_epoch, (int, float)):
        dt = datetime.fromtimestamp(dt_or_epoch, tz=timezone.utc)
    else:
        dt = dt_or_epoch
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ============================
# ===  Input builder       ===
# ============================
def _build_input(stage, last_touch, wip_lock, money_target):
    """Build a partial input: only non-None fields, so update stays partial
    and create does not send junk."""
    inp = {}
    if stage is not None:
        stage = stage.upper()
        if stage not in VALID_STAGES:
            raise TwentyError(f"bad stage {stage}")
        inp["stage"] = stage
    if last_touch is not None:
        inp["anchor"] = _iso(last_touch)            # anchor == our last-touch (DATE_TIME)
    if wip_lock is not None:
        inp["wipLock"] = bool(wip_lock)
    if money_target is not None:
        # money_target = (amount_units, "USD"); 1 unit = 1_000_000 micros
        amt, cur = money_target
        inp["moneyTarget"] = {
            # round AFTER scaling so sub-unit targets survive: (1.5,'USD') ->
            # 1_500_000 micros, not 1_000_000. int(amt) truncated the fraction.
            "amountMicros": str(round(float(amt) * 1_000_000)),
            "currencyCode": cur,
        }
    return inp


# ============================
# ===  Read                ===
# ============================
def list_tracks(first=200, stage=None):
    """Portfolio slice for digest/sync. Returns unwrapped nodes (not Relay).

    Optional stage filter (e.g. stage="ACTIVE") for the WIP check.
    """
    if stage is not None:
        stage = stage.upper()
        if stage not in VALID_STAGES:
            raise TwentyError(f"bad stage {stage}")
        Q = ("query($first:Int,$f:TrackFilterInput){ "
             "tracks(filter:$f, first:$first){ edges{ node{ "
             "id name stage anchor wipLock updatedAt } } } }")
        data = _gql(Q, {"first": first, "f": {"stage": {"eq": stage}}})
    else:
        Q = ("query($first:Int){ "
             "tracks(first:$first){ edges{ node{ "
             "id name stage anchor wipLock updatedAt } } } }")
        data = _gql(Q, {"first": first})
    return [e["node"] for e in data.get("tracks", {}).get("edges", [])]


def find_by_name(name):
    """Find a track by exact name (TEXT scalar -> direct {name:{eq}}).

    Returns the node dict or None. Idempotency for upsert lives here.

    The live instance enforces NO name-uniqueness constraint (verified: two
    createTrack with the same name both succeed). So we fetch first:2 and, if
    duplicates already exist, deterministically pick the oldest-by-id and treat
    it as THE record — upsert then updates it and never creates another. This
    keeps reruns convergent even after a duplicate slipped in. We never delete.
    """
    Q = ("query($f:TrackFilterInput){ "
         "tracks(filter:$f, first:2){ edges{ node{ "
         "id name stage anchor wipLock } } } }")
    data = _gql(Q, {"f": {"name": {"eq": name}}})
    edges = data.get("tracks", {}).get("edges", [])
    if not edges:
        return None
    if len(edges) > 1:
        # duplicates exist (no DB uniqueness) -> pick deterministically (oldest
        # id wins), so concurrent runs converge on the same survivor. Fact-only
        # log, no secrets.
        nodes = sorted((e["node"] for e in edges), key=lambda n: n["id"])
        print(f"[twenty] WARN duplicate track name {name!r}: "
              f"{len(edges)}+ rows, updating oldest id", flush=True)
        return nodes[0]
    return edges[0]["node"]


# ============================
# ===  Write (idempotent)  ===
# ============================
def upsert_track(name, stage=None, last_touch=None, wip_lock=None, money_target=None):
    """Idempotent-by-name upsert — the M1 core.

    Find by name -> update by id; else create with name. Returns the node.
    `name` is a SOFT key (no DB uniqueness, see find_by_name) — do not run the
    seed while the live loop is also upserting the same names (apply_steps
    serializes them).
    """
    inp = _build_input(stage, last_touch, wip_lock, money_target)
    node = find_by_name(name)
    if node:
        if not inp:                       # nothing to change -> skip the network
            return node
        Q = ("mutation($id:UUID!,$data:TrackUpdateInput!){ "
             "updateTrack(id:$id,data:$data){ id name stage anchor wipLock } }")
        data = _gql(Q, {"id": node["id"], "data": inp})
        # _gql collapses a null/empty data (200 w/o top-level errors) to {} — use
        # .get so that case raises the single TwentyError contract, not a bare
        # KeyError. Message is the field name only (no secret).
        out = data.get("updateTrack")
        if out is None:
            raise TwentyError("empty mutation result")
        return out
    inp["name"] = name                    # name is required on create
    Q = ("mutation($data:TrackCreateInput!){ "
         "createTrack(data:$data){ id name stage anchor wipLock } }")
    data = _gql(Q, {"data": inp})
    out = data.get("createTrack")
    if out is None:
        raise TwentyError("empty mutation result")
    return out


def set_stage(name, stage):
    """Thin wrapper: set a track's stage (used by STATE sync / KILLED/FROZEN)."""
    return upsert_track(name, stage=stage)


def touch(name, when=None, stage="ACTIVE"):
    """Sugar for a work-verdict: bump last-touch + mark ACTIVE.

    ONLY structure goes out: name + stage + anchor. No ocr_text/title/heartbeat.
    """
    return upsert_track(name, stage=stage, last_touch=(when or time.time()))


# ============================
# ===  Seed (one-shot)     ===
# ============================
def seed(items):
    """One-shot M1 seed. items = [(name, stage), ...]; idempotent per name.

    Uses single upserts (not a createTracks batch) so reruns never duplicate.
    Run ONLY while the live loop is stopped (apply_steps), because name has no
    DB uniqueness and a concurrent loop upsert could race into a duplicate.
    """
    out = []
    for name, stage in items:
        out.append(upsert_track(name, stage=stage))
    return out


# ============================
# ===  Digest helper       ===
# ============================
def digest_snapshot():
    """Portfolio facts for the digest mirror: [{name, stage, anchor}] sorted by
    anchor desc. No id / wipLock / screen text — secretaryd phrases the message.
    """
    rows = list_tracks()
    rows.sort(key=lambda t: (t.get("anchor") or ""), reverse=True)
    return [{"name": t.get("name"), "stage": t.get("stage"), "anchor": t.get("anchor")}
            for t in rows]


# ============================
# ===  CLI: one-shot seed  ===
# ============================
SEED = [
    ("secretary",    "ACTIVE"),
    ("aidrc",        "ACTIVE"),
    ("typst-studio", "BACKLOG"),
    ("voidglass",    "BACKLOG"),
    ("diagram-app",  "FROZEN"),
]

if __name__ == "__main__":
    # `python3.13 twenty_client.py seed` — idempotent portfolio seed.
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        for node in seed(SEED):
            print(f"  {node.get('name')} -> {node.get('stage')}", flush=True)
        print("seed done.", flush=True)
    else:
        for t in digest_snapshot():
            print(f"  {t['name']} — {t['stage']} (anchor {t['anchor']})", flush=True)
