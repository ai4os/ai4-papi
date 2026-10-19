"""
Minimal client for the Grafana Mimir (Prometheus-compatible) HTTP API.

Used by the energy-accounting feature to read per-allocation power time series.
API reference: https://prometheus.io/docs/prometheus/latest/querying/api/
"""

import datetime
import os
import time

import requests

MIMIR_URL = "https://mimir.k8s.cloud.ai4eosc.eu/prometheus"
# Optional: the energy-accounting feature is disabled if these are not set.
MIMIR_USER = os.environ.get("MIMIR_USER")
MIMIR_PASSWORD = os.environ.get("MIMIR_PASSWORD")
if not (MIMIR_USER and MIMIR_PASSWORD):
    print("You should define MIMIR_USER / MIMIR_PASSWORD to enable energy stats")

# Mimir keeps each series under a point cap per query; stay well below it.
MAX_POINTS_PER_QUERY = 11_000
# Measured against the real Mimir: a 24h range query takes under 1 s and a full
# 11,000-point chunk about 1.5 s, so these leave a wide margin while a hung Mimir
# does not hold a request or the sweep thread for a minute.

session = requests.Session()

# After a timeout or connection error Mimir is not asked again for this long (the
# calls fail fast with `MimirError`), so a hung Mimir costs one timeout per minute
# instead of one per query.
BACKOFF_SECONDS = 60
_down_until = 0.0


class MimirError(RuntimeError):
    """Raised when Mimir returns a non-successful response."""


def _auth():
    return (MIMIR_USER, MIMIR_PASSWORD) if MIMIR_USER else None


def _get(path: str, params: dict, timeout: int) -> list:
    """GET a Prometheus API route and return `data.result`."""
    global _down_until
    if time.monotonic() < _down_until:
        raise MimirError("Mimir unreachable, retrying shortly")
    try:
        r = session.get(
            f"{MIMIR_URL}{path}", params=params, auth=_auth(), timeout=timeout
        )
    except (requests.ConnectionError, requests.Timeout):
        _down_until = time.monotonic() + BACKOFF_SECONDS
        raise
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        raise MimirError(f"Mimir query failed: {body}")
    return body.get("data", {}).get("result", [])


def query(promql: str, at: datetime.datetime | None = None, timeout: int = 10):
    """
    Instantaneous query (`/api/v1/query`). Returns the raw `data.result` list
    (vector: one entry per series, each with `metric` and `value` = [ts, str]).
    """
    params = {"query": promql}
    if at is not None:
        params["time"] = at.timestamp()
    return _get("/api/v1/query", params, timeout)


def query_range(
    promql: str,
    start: datetime.datetime,
    end: datetime.datetime,
    step_s: int = 30,
    timeout: int = 15,
):
    """
    Range query (`/api/v1/query_range`). Returns the raw `data.result` list
    (matrix: one entry per series, each with `metric` and `values` =
    [[ts, str], ...]).
    """
    params = {
        "query": promql,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "step": f"{step_s}s",
    }
    return _get("/api/v1/query_range", params, timeout)


def _split_chunks(start, end, step_s, max_points):
    """
    Yield non-overlapping (chunk_start, chunk_end) tuples so each range query
    stays under `max_points` samples per series (relevant when backfilling a
    deployment whose pointer is far behind).
    """
    while start < end:
        remaining_steps = int((end - start).total_seconds() / step_s)
        chunk_steps = min(remaining_steps, max_points)
        if chunk_steps <= 0:
            break
        chunk_end = start + datetime.timedelta(seconds=chunk_steps * step_s)
        yield start, min(chunk_end, end)
        start = chunk_end + datetime.timedelta(seconds=step_s)


def query_range_chunked(
    promql: str,
    start: datetime.datetime,
    end: datetime.datetime,
    step_s: int = 30,
    max_points: int = MAX_POINTS_PER_QUERY,
):
    """
    Like `query_range` but splits long windows into consecutive chunks and
    merges the results, concatenating each series' `values` sorted by timestamp.
    """
    merged: dict = {}
    for chunk_start, chunk_end in _split_chunks(start, end, step_s, max_points):
        for series in query_range(promql, chunk_start, chunk_end, step_s):
            key = frozenset(series.get("metric", {}).items())
            merged.setdefault(key, {"metric": series.get("metric", {}), "values": []})
            merged[key]["values"].extend(series.get("values", []))

    result = []
    for entry in merged.values():
        entry["values"].sort(key=lambda v: float(v[0]))
        result.append(entry)
    return result
