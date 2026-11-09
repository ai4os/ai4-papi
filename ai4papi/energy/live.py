"""
Live top-up of the energy-accounting feature (read path).

The sweep only reaches `pointer_ts` (up to one sweep interval behind). On each
API read, `topup(doc)` adds the `pointer_ts -> now` slice live from Mimir so the
dashboard counter moves every ~30s, and `live_series(doc)` returns that same
slice as time-series points. Both work on a deployment doc and on a datacenter
doc (`namespace == CLUSTER_NS`).
"""

import logging
import threading

from cachetools import cached

from ai4papi.energy import compute, config, query
from ai4papi.wattnet import green_director

LOG = logging.getLogger(__name__)

_live_cache = config.topup_cache(8192)


def _live_window(pointer_iso: str) -> tuple[float, float]:
    """
    `[pointer_ts, now]`, clamped on the left so a long-dormant pointer does not
    trigger a huge query. No ingestion-lag cushion: Mimir data is immutable
    once ingested (never revised), so querying right up to "now" cannot return
    a wrong value -- worst case, a sample scraped a moment ago is not visible
    *yet* and simply is not the one returned as "last", which self-corrects on
    the very next call. Used by `topup` for both a real, persisted accumulator
    and `live_only_doc`'s transient one (nothing persisted): both get the
    freshest number Mimir can currently give.
    """
    cfg = config.get()
    end_ts = config.now().timestamp()
    start_ts = max(
        compute.parse_iso(pointer_iso),
        end_ts - (cfg["sweep_seconds"] + 4 * cfg["series_bucket_seconds"]),
    )
    return start_ts, end_ts


def _running_key(doc: dict | None) -> tuple | None:
    """Hashable summary of what a live query depends on; `None` if there is
    nothing to query (no data yet, or a deployment with no running allocation)."""
    if not doc:
        return None
    pointer = doc.get("pointer_ts") or doc.get("settled_ts")
    if not doc.get("metrics_available", False) or not pointer:
        return None
    if doc.get("namespace") == config.CLUSTER_NS:
        return (config.CLUSTER_NS, doc["deployment_uuid"], pointer, ())
    running = doc.get("active_allocs") or []
    if not running:
        return None
    return (
        doc["namespace"],
        doc["deployment_uuid"],
        pointer,
        tuple(sorted((a["alloc_id"], a["datacenter"]) for a in running)),
    )


def _sources(key: tuple) -> list[tuple]:
    """`(dc, cpu_query, gpu_query, data_quality)` per datacenter."""
    ns, uuid, _pointer, alloc_pairs = key
    if ns == config.CLUSTER_NS:
        return [(uuid, *compute.cluster_promql(uuid), config.cluster_dq())]

    by_dc: dict[str, list[str]] = {}
    for alloc_id, dc in alloc_pairs:
        by_dc.setdefault(dc, []).append(alloc_id)
    dq = config.get().get("data_quality")
    return [(dc, *compute.power_promql(ids), dq) for dc, ids in by_dc.items()]


def _live_buckets(key: tuple, start_ts: float, end_ts: float, bucket_s: int):
    """
    Integrate the live window from Mimir into `bucket_s` buckets (per DC, merged),
    plus the current draw from the last sample of that same query. Returns
    `(buckets, power_w)`; `power_w` is `None` only when a series has no sample
    at all in the window (nothing to read yet), never because the last sample
    is "too old" -- Mimir data is immutable once ingested, so the actual last
    point is always the right one to show, however old it is.
    """
    step_s = int(config.get()["query_step_seconds"])
    if end_ts - start_ts < step_s:
        return [], None

    per_dc = []
    inst_w = 0.0
    any_inst = False
    for dc, cpu_q, gpu_q, dq in _sources(key):
        cpu, gpu = query.fetch_power(cpu_q, gpu_q, start_ts, end_ts, step_s)
        tue = config.tue(dc)
        buckets = compute.integrate(cpu, gpu, step_s, bucket_s, tue, data_quality=dq)
        metrics = green_director.metrics.get(dc, {})
        per_dc.append(
            compute.apply_footprint(
                buckets, metrics.get("carbon"), metrics.get("water")
            )
        )
        iw = compute.instant_power(cpu, gpu, tue, dq)
        if iw is not None:
            inst_w += iw
            any_inst = True

    return compute.merge_buckets(per_dc), (inst_w if any_inst else None)


def topup(doc: dict | None) -> dict | None:
    """
    Live `pointer_ts -> now` slice as a single delta: dict with the summed energy
    deltas, `power_w` (the last sample read, i.e. the current draw) and
    `live_as_of`; `{"degraded": True}` on failure; None if not applicable.
    Cached for `topup_cache_seconds`.
    """
    key = _running_key(doc)
    if key is None:
        return None
    try:
        return _compute_topup(key)
    except Exception:
        LOG.warning(
            "energy top-up failed for %s",
            config.log_safe(doc.get("deployment_uuid")),
            exc_info=True,
        )
        return {"degraded": True}


@cached(_live_cache, lock=threading.Lock())
def _compute_topup(key: tuple) -> dict:
    start_ts, end_ts = _live_window(key[2])
    result = {
        **config.EMPTY,
        "power_w": None,
        "live_as_of": compute.iso(end_ts),
        "degraded": False,
    }
    span = int(end_ts - start_ts) + int(config.get()["query_step_seconds"])
    buckets, inst_w = _live_buckets(key, start_ts, end_ts, bucket_s=max(span, 1))
    for b in buckets:
        for k in config.KEYS:
            result[k] += getattr(b, k)
    # `power_w` is the last sample read (current draw), not a window mean
    result["power_w"] = inst_w
    return result


def live_series(doc: dict | None, bucket_s: int) -> list[dict]:
    """
    Live tail as time-series points (so the detail-view graph keeps growing).
    `[]` if not applicable or on any failure.
    """
    key = _running_key(doc)
    if key is None:
        return []
    start_ts, end_ts = _live_window(key[2])
    try:
        buckets, _ = _live_buckets(key, start_ts, end_ts, bucket_s=max(bucket_s, 1))
    except Exception:
        LOG.warning(
            "energy live_series failed for %s",
            config.log_safe(doc.get("deployment_uuid")),
        )
        return []
    return [{**b._asdict(), "live": True} for b in buckets]
