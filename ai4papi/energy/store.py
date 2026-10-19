"""
File-based store for the energy-accounting feature.

One JSON document per deployment under `$ACCOUNTING_PTH/energy/<namespace>/`,
plus an append-only `.series.jsonl` with the consolidated 15-min buckets
(`energy.series_bucket_seconds`). The per-datacenter accumulators live the same
way under the `_cluster` pseudo-namespace. Same convention as
`ai4papi.utils.retrieve_from_snapshots`. The interface is deliberately small so
it can be swapped for a database later.
"""

import json
import os
import re
import threading
import time
from pathlib import Path


def base_dir() -> Path | None:
    """`$ACCOUNTING_PTH/energy`, or None if accounting storage is not configured."""
    root = os.environ.get("ACCOUNTING_PTH")
    if not root:
        return None
    return Path(root) / "energy"


# A namespace or deployment id is used as a file name: only a plain name is allowed
# (no separators, no leading dot), so an id can never point outside the store.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def _name(value: str) -> str | None:
    return os.path.basename(value) if _SAFE_NAME.fullmatch(value) else None


def _ns_dir(ns: str) -> Path | None:
    base, name = base_dir(), _name(ns)
    return (base / name) if base and name else None


def _accum_path(ns: str, uuid: str) -> Path | None:
    d, name = _ns_dir(ns), _name(uuid)
    return (d / f"{name}.accum.json") if d and name else None


def _series_path(ns: str, uuid: str) -> Path | None:
    d, name = _ns_dir(ns), _name(uuid)
    return (d / f"{name}.series.jsonl") if d and name else None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# accumulator document
# ---------------------------------------------------------------------------


def read_accum(ns: str, uuid: str) -> dict | None:
    path = _accum_path(ns, uuid)
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_accum(ns: str, uuid: str, doc: dict) -> None:
    path = _accum_path(ns, uuid)
    if not path:
        return
    _atomic_write(path, json.dumps(doc, separators=(",", ":")))
    with _owner_lock:
        entry = _owner_idx.get(ns)
        if entry:
            entry[1].setdefault(doc.get("owner"), set()).add(uuid)


def iter_namespace(ns: str):
    """Yield (uuid, doc) for every accumulator document in the namespace."""
    d = _ns_dir(ns)
    if not d or not d.is_dir():
        return
    for path in d.glob("*.accum.json"):
        uuid = path.name[: -len(".accum.json")]
        try:
            yield uuid, json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


# owner -> uuids of a namespace, so listing one user's deployments does not parse
# every document of the namespace. Kept in sync by `write_accum` and rebuilt from
# disk every `_OWNER_IDX_TTL` seconds (heals anything written by another process).
# shortcut: assumes one PAPI process writes the store, upgrade to a database index
# if PAPI runs as several replicas on the same storage.
_OWNER_IDX_TTL = 300
_owner_idx: dict[str, tuple[float, dict[str | None, set[str]]]] = {}
_owner_lock = threading.Lock()


def _owner_uuids(ns: str, owner: str) -> list[str]:
    with _owner_lock:
        entry = _owner_idx.get(ns)
        if entry is None or time.monotonic() - entry[0] > _OWNER_IDX_TTL:
            idx: dict[str | None, set[str]] = {}
            for uuid, doc in iter_namespace(ns):
                idx.setdefault(doc.get("owner"), set()).add(uuid)
            entry = _owner_idx[ns] = (time.monotonic(), idx)
        return sorted(entry[1].get(owner, ()))


def iter_owner(ns: str, owner: str):
    """Yield (uuid, doc) for the accumulator documents owned by `owner`."""
    for uuid in _owner_uuids(ns, owner):
        doc = read_accum(ns, uuid)
        if doc is not None and doc.get("owner") == owner:
            yield uuid, doc


# ---------------------------------------------------------------------------
# consolidated time series (append-only .series.jsonl)
# ---------------------------------------------------------------------------


def append_series(ns: str, uuid: str, buckets: list[dict]) -> None:
    path = _series_path(ns, uuid)
    if not path or not buckets:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(json.dumps(b, separators=(",", ":")) + "\n" for b in buckets)


def read_series(ns: str, uuid: str, start_ts: str | None, end_ts: str | None):
    path = _series_path(ns, uuid)
    if not path or not path.is_file():
        return []
    out: dict[str, dict] = {}  # by ts: a bucket appended twice counts once
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                b = json.loads(line)
            except json.JSONDecodeError:
                continue
            if start_ts and b["ts"] < start_ts:
                continue
            if end_ts and b["ts"] > end_ts:
                continue
            out[b["ts"]] = b
    return [out[ts] for ts in sorted(out)]
