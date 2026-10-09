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
from pathlib import Path


def base_dir() -> Path | None:
    """`$ACCOUNTING_PTH/energy`, or None if accounting storage is not configured."""
    root = os.environ.get("ACCOUNTING_PTH")
    if not root:
        return None
    return Path(root) / "energy"


def _ns_dir(ns: str) -> Path | None:
    base = base_dir()
    return (base / ns) if base else None


def _accum_path(ns: str, uuid: str) -> Path | None:
    d = _ns_dir(ns)
    return (d / f"{uuid}.accum.json") if d else None


def _series_path(ns: str, uuid: str) -> Path | None:
    d = _ns_dir(ns)
    return (d / f"{uuid}.series.jsonl") if d else None


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


def iter_owner(ns: str, owner: str):
    """Yield (uuid, doc) for the accumulator documents owned by `owner`."""
    for uuid, doc in iter_namespace(ns):
        if doc.get("owner") == owner:
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
        for b in buckets:
            f.write(json.dumps(b, separators=(",", ":")) + "\n")


def read_series(ns: str, uuid: str, start_ts: str | None, end_ts: str | None):
    path = _series_path(ns, uuid)
    if not path or not path.is_file():
        return []
    out = []
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
            out.append(b)
    out.sort(key=lambda b: b["ts"])
    return out
