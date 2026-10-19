"""
Energy-accounting read API used by the routers.

* `get_accumulated` / `get_accumulated_bulk`: per-deployment accumulated energy,
  carbon and water (persisted accumulator + live Mimir top-up).
* `get_series`: per-deployment time series for the detail-view graphs (persisted
  15-min archive + a live tail so the graph keeps growing).
* `get_user_energy`: per-user aggregate (all of the user's deployments summed,
  each with its live top-up), optionally with a merged time series.
* `get_datacenter_power` / `get_cluster_energy`: per-datacenter instantaneous
  power and accumulated energy for `/stats/cluster` (no platform-wide total).

Everything degrades to `None` / empty when the feature is disabled or the data
is not available; it never raises to the caller.
"""

import datetime
import logging
import threading

from cachetools import TTLCache, cached

from ai4papi.clients import mimir
from ai4papi.energy import compute, config, store
from ai4papi.energy.series import build_series, persisted
from ai4papi.energy.config import CLUSTER_NS, KEYS, enabled
from ai4papi.energy.live import live_series, topup
from ai4papi.energy.ondemand import ensure_swept, ensure_swept_bulk, live_only_doc
from ai4papi.energy.sweep import process_single, run_sweep

__all__ = [
    "add_to_job",
    "add_to_jobs",
    "enabled",
    "get_accumulated",
    "get_accumulated_bulk",
    "get_cluster_energy",
    "get_datacenter_power",
    "get_series",
    "get_user_energy",
    "process_single",
    "run_sweep",
]

LOG = logging.getLogger(__name__)


@cached(cache=TTLCache(maxsize=8192, ttl=60), lock=threading.Lock())
def _read_doc(namespace: str, uuid: str):
    return store.read_accum(namespace, uuid)


def _doc_tue(doc: dict) -> float | None:
    """`tue_factor` from the doc; for a doc written before that field existed,
    the single datacenter's configured TUE (blended docs then read `None`)."""
    if doc.get("tue_factor"):
        return doc["tue_factor"]
    dcs = doc.get("datacenters") or []
    return config.tue(dcs[0]) if len(dcs) == 1 else None


def _to_stats(doc: dict) -> dict:
    acc = doc.get("accumulated") or {}
    # fallback power for a running deployment with no live top-up: the last
    # persisted bucket's mean. `_apply_topup` replaces it with the actual last
    # Mimir sample (current draw) whenever the top-up succeeds.
    tail = doc.get("tail") or []
    running = doc.get("status") == "running"
    last = tail[-1] if tail else {}
    return {
        "energy_wh": acc.get("energy_wh", 0.0),
        "tue_factor": _doc_tue(doc),
        "carbon_g": acc.get("carbon_g", 0.0),
        "water_l": acc.get("water_l", 0.0),
        "power_w": last.get("power_w_avg") if running else None,
        "since": doc.get("window_start") or doc.get("settled_ts"),
        "as_of": doc.get("pointer_ts"),
        "live_as_of": None,
        "complete": doc.get("complete", True),
        "coverage_ratio": doc.get("coverage_ratio", 1.0),
        "degraded": False,
        "datacenters": doc.get("datacenters", []),
    }


def _apply_topup(stats: dict, doc: dict) -> dict:
    t = topup(doc)
    if not t:
        return stats
    if t.get("degraded"):
        stats["degraded"] = True
        return stats
    # the top-up slice uses the same TUE as the accumulator, so `tue_factor`
    # does not move; just add the energy / footprint deltas and refresh power.
    for k in KEYS:
        stats[k] += t.get(k, 0.0)
    if t.get("power_w") is not None:
        stats["power_w"] = t["power_w"]
    stats["live_as_of"] = t.get("live_as_of")
    return stats


def _live_only_stats(namespace: str, uuid: str) -> tuple[dict, dict] | None:
    """
    `(doc, stats)` for a deployment with no accumulator doc yet, computed purely
    from `live_only_doc` + its top-up. `None` whenever there is nothing to show:
    no running allocation, Mimir unreachable (`degraded`), or Mimir simply has
    not returned a single sample for this allocation yet -- an all-zero top-up
    with no instantaneous power is indistinguishable from "no measurement", so
    it is treated as not populated rather than shown as a fabricated `0` reading.
    """
    doc = live_only_doc(namespace, uuid)
    if doc is None:
        return None
    stats = _apply_topup(_to_stats(doc), doc)
    has_data = stats.get("power_w") is not None or any(stats.get(k, 0.0) for k in KEYS)
    if not has_data:
        return None
    return doc, stats


def _resolve(namespace: str, uuid: str, live: bool) -> tuple[dict, dict] | None:
    """
    `(doc, stats)` of one deployment, or `None` when there is nothing to report.
    The stored doc is swept on demand when missing; without a usable doc the
    number comes from Mimir alone (`live_only_doc`).
    """
    doc = _read_doc(namespace, uuid)
    if doc is None:
        # never swept: run one sweep pass for just this deployment instead of
        # waiting up to `sweep_seconds` for the background thread to reach it.
        doc = ensure_swept(namespace, uuid)
    if doc is not None and doc.get("metrics_available") is False:
        # confirmed: ran a significant time with no usable data. Do not keep
        # retrying live -- wait for the real sweep to reassess it.
        return None
    if doc is None or "accumulated" not in doc:
        # nothing settled yet: either too young for a bucket, or an
        # inconclusive on-demand stub written before Nomad had even placed the
        # allocation (no `metrics_available` verdict either way). Fall back to
        # a pure-Mimir number, but only once Mimir actually has a sample for
        # it - never a fabricated zero.
        return _live_only_stats(namespace, uuid) if live else None
    stats = _to_stats(doc)
    return doc, (_apply_topup(stats, doc) if live else stats)


def _owner_rows(namespace: str, owner: str, live: bool) -> list[tuple[str, dict, dict]]:
    """`(uuid, doc, stats)` of every accounted deployment of `owner` in `namespace`,
    sweeping the missing ones on demand first."""
    live_jobs = ensure_swept_bulk(namespace, owner)
    rows: list[tuple[str, dict, dict]] = []
    covered: set[str] = set()
    for uuid, doc in store.iter_owner(namespace, owner):
        if doc.get("metrics_available") is False:
            covered.add(uuid)
            continue
        if "accumulated" not in doc:
            continue  # inconclusive stub: leave uncovered, try live-only below
        covered.add(uuid)
        stats = _to_stats(doc)
        rows.append((uuid, doc, _apply_topup(stats, doc) if live else stats))
    if live:
        for stub in live_jobs:
            uuid = stub["ID"]
            if uuid in covered:
                continue
            res = _live_only_stats(namespace, uuid)
            if res is not None:
                rows.append((uuid, *res))
    return rows


def get_accumulated(namespace: str, uuid: str, live: bool = True) -> dict | None:
    if not enabled():
        return None
    res = _resolve(namespace, uuid, live)
    return res[1] if res else None


def get_accumulated_bulk(
    namespace: str, owner: str, live: bool = True
) -> dict[str, dict]:
    if not enabled():
        return {}
    return {uuid: stats for uuid, _doc, stats in _owner_rows(namespace, owner, live)}


def add_to_job(job: dict, namespace: str, full_info: bool) -> None:
    """Set `job["energy"]`: the extended time series if `full_info`, else just the
    accumulated stats."""
    get = get_series if full_info else get_accumulated
    job["energy"] = get(namespace, job["job_ID"])


def add_to_jobs(jobs: list[dict], namespace: str, owner: str, full_info: bool) -> None:
    """`add_to_job` for all the `owner`'s jobs of a namespace. Without `full_info`
    it uses one batch of cheap file reads and a single cached Mimir top-up;
    with it, the extended time series per deployment (heavier)."""
    if not jobs:
        return
    if full_info:
        for job in jobs:
            add_to_job(job, namespace, full_info)
    else:
        stats = get_accumulated_bulk(namespace, owner)
        for job in jobs:
            job["energy"] = stats.get(job["job_ID"])


def _aggregate(stats_list: list[dict]) -> dict:
    """Sum the `EnergyStats` of several deployments into one block."""
    agg = dict.fromkeys(KEYS, 0.0)
    dcs: set[str] = set()
    power = raw_energy = 0.0  # raw_energy: Σ energy_wh / tue, to blend `tue_factor`
    since, as_of, live_as_of, coverage = [], [], [], []
    for s in stats_list:
        for k in KEYS:
            agg[k] += s.get(k, 0.0) or 0.0
        if s.get("tue_factor"):
            raw_energy += s["energy_wh"] / s["tue_factor"]
        dcs.update(s.get("datacenters") or [])
        power += s.get("power_w") or 0.0
        since += [s["since"]] if s.get("since") else []
        as_of += [s["as_of"]] if s.get("as_of") else []
        live_as_of += [s["live_as_of"]] if s.get("live_as_of") else []
        coverage.append(s.get("coverage_ratio", 1.0))

    return {
        # not rounded, same as the per-deployment `EnergyStats`: the values can be
        # legitimately tiny (idle CPU workloads) and must reconcile with the sum
        # of `series`.
        **agg,
        "tue_factor": (agg["energy_wh"] / raw_energy) if raw_energy else None,
        "power_w": power or None,
        # time range these numbers cover: [since, as_of], live top-up to live_as_of
        "since": min(since, default=None),
        "as_of": max(as_of, default=None),
        "live_as_of": max(live_as_of, default=None),
        "complete": all(s.get("complete", True) for s in stats_list),
        "coverage_ratio": round(sum(coverage) / len(coverage), 3),
        "degraded": any(s.get("degraded") for s in stats_list),
        "datacenters": sorted(dcs),
        "deployments": len(stats_list),
    }


def get_user_energy(
    namespace: str, owner: str, series: bool = False, target_points: int | None = None
) -> dict | None:
    """
    All of a user's deployments in a VO rolled into one `EnergyStats`-shaped
    block: each deployment's persisted accumulator plus its live Mimir top-up,
    summed. With `series=True`, also a 15-min time series merged across the
    deployments (`full_info=true` on `GET /stats/user`). `None` when the user has
    no accounted deployment.
    """
    if not enabled():
        return None
    rows = _owner_rows(namespace, owner, live=True)
    if not rows:
        return None

    out = _aggregate([stats for _uuid, _doc, stats in rows])
    out["series"] = None
    if series:
        cfg = config.get()
        step_s = int(cfg["series_bucket_seconds"])
        if target_points is None:
            target_points = int(cfg.get("user_series_points", 1000))
        out["series"] = (
            build_series(
                [
                    persisted(store.read_series(namespace, uuid, None, None), doc)
                    for uuid, doc, _stats in rows
                ],
                [live_series(doc, bucket_s=step_s) for _uuid, doc, _stats in rows],
                target_points,
            )
            or None
        )
    return out


# ---------------------------------------------------------------------------
# cluster-level instantaneous power / footprint (for /stats/cluster)
# ---------------------------------------------------------------------------


def _sum_by_datacenter(promql: str, scale: float) -> dict[str, float]:
    """Instant Mimir query grouped `by (datacenter)` -> {dc: value * scale}."""
    out: dict[str, float] = {}
    for series in mimir.query(promql):
        dc = series.get("metric", {}).get("datacenter")
        if not dc:
            continue
        try:
            out[dc] = float(series["value"][1]) * scale
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return out


@cached(cache=TTLCache(maxsize=1, ttl=25), lock=threading.Lock())
def get_datacenter_power() -> dict[str, dict]:
    """
    Instantaneous power draw per datacenter, normalized by TUE:
    `power_w = (sum host power + sum GPU power) * TUE[dc]`. Best-effort: an
    unreachable Mimir yields `{}`, and a datacenter with no power series at all
    is simply absent, never reported as zero. A datacenter listed in
    `energy.metrics_datacenters` counts with whatever it has (host-only,
    GPU-only or both); without that allowlist a host-power series is required so
    a stray GPU metric does not fabricate a total. `{}` when disabled.
    """
    if not enabled():
        return {}

    metrics_dcs = set(config.get().get("metrics_datacenters") or [])
    try:
        host_w = _sum_by_datacenter(
            "sum by (datacenter) (scaph_host_power_microwatts)", 1e-6
        )
        gpu_w = _sum_by_datacenter("sum by (datacenter) (DCGM_FI_DEV_POWER_USAGE)", 1.0)
    except (mimir.MimirError, OSError):
        LOG.warning("mimir cluster-power query failed")
        return {}

    as_of = compute.iso(datetime.datetime.now(datetime.timezone.utc).timestamp())
    out: dict[str, dict] = {}
    for dc in set(host_w) | set(gpu_w):
        if metrics_dcs:
            if dc not in metrics_dcs:
                continue
            # declared site: host-only, GPU-only or both are all valid totals
        elif dc not in host_w:
            # no allowlist: require host power so a stray GPU metric for an
            # unmonitored datacenter does not fabricate a total
            continue
        tue = config.tue(dc)
        out[dc] = {
            "power_w": (host_w.get(dc, 0.0) + gpu_w.get(dc, 0.0)) * tue,
            "tue_factor": tue,
            "as_of": as_of,
        }
    return out


@cached(cache=TTLCache(maxsize=16, ttl=300), lock=threading.Lock())
def _cluster_archive_series(dc: str) -> list[dict]:
    """
    Consolidated 15-min buckets of a datacenter (the `.series.jsonl`). Cached for
    ~5 min: it only changes when a bucket ages out of the 24h tail, which happens
    at sweep cadence, so the 30s `get_cluster_stats` thread need not re-read it.
    """
    return store.read_series(CLUSTER_NS, dc, None, None)


def get_cluster_energy(
    series: bool = True, target_points: int | None = None
) -> dict[str, dict]:
    """
    Per-datacenter energy for `/stats/cluster`: the instantaneous power from
    `get_datacenter_power()` merged with the persisted accumulator plus a live
    top-up, plus a downsampled 15-min `series`. `{}` when the feature is disabled.
    A datacenter shows up if it has instantaneous power or an accumulator doc.
    """
    if not enabled():
        return {}
    cfg = config.get()
    if target_points is None:
        target_points = int(cfg.get("cluster_series_points", 500))
    bucket_s = int(cfg["series_bucket_seconds"])

    inst = get_datacenter_power()
    metrics_dcs = cfg.get("metrics_datacenters") or []
    out: dict[str, dict] = {}

    for dc in set(inst) | set(metrics_dcs):
        entry = dict(inst.get(dc) or {})
        doc = store.read_accum(CLUSTER_NS, dc)
        if doc and doc.get("accumulated") and doc.get("metrics_available") is not False:
            acc = {k: doc["accumulated"].get(k, 0.0) for k in KEYS}
            live_as_of, degraded = None, False
            t = topup(doc)
            if t and t.get("degraded"):
                degraded = True
            elif t:
                for k in KEYS:
                    acc[k] += t.get(k, 0.0)
                live_as_of = t.get("live_as_of")
            # not rounded: consistent with the per-deployment / per-user blocks,
            # and the total must reconcile with the sum of `series`.
            entry.update(
                energy_wh=acc["energy_wh"],
                carbon_g=acc["carbon_g"],
                water_l=acc["water_l"],
                tue_factor=doc.get("tue_factor") or config.tue(dc),
                complete=doc.get("complete", True),
                since=doc.get("window_start") or doc.get("settled_ts"),
                as_of=doc.get("pointer_ts"),
                live_as_of=live_as_of,
                degraded=degraded,
            )
            if series:
                entry["series"] = build_series(
                    [persisted(_cluster_archive_series(dc), doc)],
                    [live_series(doc, bucket_s=bucket_s)],
                    target_points,
                )
        if entry:
            out[dc] = entry
    return out


# ---------------------------------------------------------------------------
# time series helpers
# ---------------------------------------------------------------------------


def get_series(
    namespace: str, uuid: str, target_points: int | None = None
) -> dict | None:
    if not enabled():
        return None
    res = _resolve(namespace, uuid, live=True)
    if res is None:
        return None
    doc, stats = res

    cfg = config.get()
    if target_points is None:
        target_points = int(cfg.get("deployment_series_points", 1000))
    step_s = int(cfg["series_bucket_seconds"])
    series = build_series(
        [persisted(store.read_series(namespace, uuid, None, None), doc)],
        [live_series(doc, bucket_s=step_s)],
        target_points,
    )

    return {
        "deployment_uuid": uuid,
        "start": series[0]["ts"] if series else (stats.get("since") or ""),
        "end": series[-1]["ts"]
        if series
        else (stats.get("live_as_of") or stats.get("as_of") or ""),
        "source": "mixed" if any(p["live"] for p in series) else "store",
        "series": series,
        "accumulated": stats,
    }
