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

from cachetools import TTLCache, cached

import ai4papi.conf as papiconf
from ai4papi import mimir
from ai4papi.accounting import compute, store
from ai4papi.accounting.sweep import (
    CLUSTER_NS,
    _tue,
    enabled,
    live_series,
    live_series_cluster,
    process_single,
    run_sweep,
    topup,
    topup_cluster,
)

__all__ = [
    "enabled",
    "run_sweep",
    "process_single",
    "get_accumulated",
    "get_accumulated_bulk",
    "get_series",
    "get_user_energy",
    "get_datacenter_power",
    "get_cluster_energy",
]

LOG = logging.getLogger(__name__)

_KEYS = ("energy_wh", "carbon_g", "water_l")  # everything is TUE-normalized


@cached(cache=TTLCache(maxsize=8192, ttl=60))
def _read_doc(namespace: str, uuid: str):
    return store.read_accum(namespace, uuid)


def _doc_tue(doc: dict) -> float | None:
    """`tue_factor` from the doc; for a doc written before that field existed,
    the single datacenter's configured TUE (blended docs then read `None`)."""
    if doc.get("tue_factor"):
        return doc["tue_factor"]
    dcs = doc.get("datacenters") or []
    return _tue(dcs[0]) if len(dcs) == 1 else None


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
    for k in _KEYS:
        stats[k] += t.get(k, 0.0)
    if t.get("power_w") is not None:
        stats["power_w"] = t["power_w"]
    stats["live_as_of"] = t.get("live_as_of")
    return stats


def get_accumulated(namespace: str, uuid: str, live: bool = True) -> dict | None:
    if not enabled():
        return None
    doc = _read_doc(namespace, uuid)
    if not doc or doc.get("metrics_available") is False or "accumulated" not in doc:
        return None
    stats = _to_stats(doc)
    return _apply_topup(stats, doc) if live else stats


def get_accumulated_bulk(
    namespace: str, owner: str, live: bool = True
) -> dict[str, dict]:
    if not enabled():
        return {}
    out: dict[str, dict] = {}
    for uuid, doc in store.iter_owner(namespace, owner):
        if doc.get("metrics_available") is False or "accumulated" not in doc:
            continue
        stats = _to_stats(doc)
        out[uuid] = _apply_topup(stats, doc) if live else stats
    return out


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

    if target_points is None:
        target_points = int(
            papiconf.MAIN_CONF["energy"].get("user_series_points", 1000)
        )

    rows = []
    for uuid, doc in store.iter_owner(namespace, owner):
        if doc.get("metrics_available") is False or "accumulated" not in doc:
            continue
        rows.append((uuid, doc, _apply_topup(_to_stats(doc), doc)))
    if not rows:
        return None

    agg = dict.fromkeys(_KEYS, 0.0)
    dcs: set[str] = set()
    power = raw_energy = 0.0  # raw_energy: Σ energy_wh / tue, to blend `tue_factor`
    since, as_of, live_as_of, coverage = [], [], [], []
    complete, degraded = True, False
    for _uuid, _doc, s in rows:
        for k in _KEYS:
            agg[k] += s.get(k, 0.0) or 0.0
        if s.get("tue_factor"):
            raw_energy += s["energy_wh"] / s["tue_factor"]
        dcs.update(s.get("datacenters") or [])
        if s.get("power_w"):
            power += s["power_w"]
        if s.get("since"):
            since.append(s["since"])
        if s.get("as_of"):
            as_of.append(s["as_of"])
        if s.get("live_as_of"):
            live_as_of.append(s["live_as_of"])
        coverage.append(s.get("coverage_ratio", 1.0))
        complete = complete and s.get("complete", True)
        degraded = degraded or bool(s.get("degraded"))

    out = {
        # not rounded, same as the per-deployment `EnergyStats`: the values can be
        # legitimately tiny (idle CPU workloads) and must reconcile with the sum
        # of `series`.
        "energy_wh": agg["energy_wh"],
        "tue_factor": (agg["energy_wh"] / raw_energy) if raw_energy else None,
        "carbon_g": agg["carbon_g"],
        "water_l": agg["water_l"],
        "power_w": power or None,
        # time range these numbers cover: [since, as_of], live top-up to live_as_of
        "since": min(since) if since else None,
        "as_of": max(as_of) if as_of else None,
        "live_as_of": max(live_as_of) if live_as_of else None,
        "complete": complete,
        "coverage_ratio": round(sum(coverage) / len(coverage), 3),
        "degraded": degraded,
        "datacenters": sorted(dcs),
        "deployments": len(rows),
        "series": None,
    }
    if series:
        out["series"] = _user_series(namespace, rows, target_points) or None
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


@cached(cache=TTLCache(maxsize=1, ttl=25))
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

    metrics_dcs = set(papiconf.MAIN_CONF["energy"].get("metrics_datacenters") or [])
    try:
        host_w = _sum_by_datacenter(
            "sum by (datacenter) (scaph_host_power_microwatts)", 1e-6
        )
        gpu_w = _sum_by_datacenter("sum by (datacenter) (DCGM_FI_DEV_POWER_USAGE)", 1.0)
    except (mimir.MimirError, OSError):
        LOG.warning("mimir cluster-power query failed")
        return {}

    as_of = compute._iso(datetime.datetime.now(datetime.timezone.utc).timestamp())
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
        tue = _tue(dc)
        out[dc] = {
            "power_w": (host_w.get(dc, 0.0) + gpu_w.get(dc, 0.0)) * tue,
            "tue_factor": tue,
            "as_of": as_of,
        }
    return out


@cached(cache=TTLCache(maxsize=16, ttl=300))
def _cluster_archive_series(dc: str) -> list[dict]:
    """
    Consolidated 15-min buckets of a datacenter (the `.series.jsonl`). Cached for
    ~5 min: it only changes when a bucket ages out of the 24h tail, which happens
    at sweep cadence, so the 30s `get_cluster_stats` thread need not re-read it.
    """
    return store.read_series(CLUSTER_NS, dc, None, None)


def _cluster_series(
    dc: str, doc: dict, bucket_s: int, target_points: int
) -> list[dict]:
    return _build_series(
        list(_cluster_archive_series(dc)) + list(doc.get("tail") or []),
        live_series_cluster(dc, doc, bucket_s=bucket_s),
        target_points,
    )


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
    cfg = papiconf.MAIN_CONF["energy"]
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
            acc = {k: doc["accumulated"].get(k, 0.0) for k in _KEYS}
            live_as_of, degraded = None, False
            t = topup_cluster(dc, doc)
            if t and t.get("degraded"):
                degraded = True
            elif t:
                for k in _KEYS:
                    acc[k] += t.get(k, 0.0)
                live_as_of = t.get("live_as_of")
            # not rounded: consistent with the per-deployment / per-user blocks,
            # and the total must reconcile with the sum of `series`.
            entry.update(
                energy_wh=acc["energy_wh"],
                carbon_g=acc["carbon_g"],
                water_l=acc["water_l"],
                tue_factor=doc.get("tue_factor") or _tue(dc),
                complete=doc.get("complete", True),
                since=doc.get("window_start") or doc.get("settled_ts"),
                as_of=doc.get("pointer_ts"),
                live_as_of=live_as_of,
                degraded=degraded,
            )
            if series:
                entry["series"] = _cluster_series(dc, doc, bucket_s, target_points)
        if entry:
            out[dc] = entry
    return out


# ---------------------------------------------------------------------------
# time series helpers
# ---------------------------------------------------------------------------


def _build_series(
    archive_and_tail: list[dict], live_points: list[dict], target_points: int
) -> list[dict]:
    """
    One entity's whole-life time series: persisted 15-min buckets (archive + the
    still-mutable tail) `compute.downsample`d to `target_points`, then the live
    Mimir tail appended un-downsampled. Shared by the per-deployment (`get_series`)
    and per-datacenter (`_cluster_series`) paths. The points carry the per-bucket
    increments only; a running total is `cumsum(series[].energy_wh)` and matches
    `accumulated.energy_wh`.
    """
    raw = sorted(archive_and_tail, key=lambda b: b["ts"])
    series = [
        _point(b, live=False)
        for b in compute.downsample([_bucket(b) for b in raw], target_points)
    ]
    series += [
        _point(_bucket(p), live=True)
        for p in live_points
        if not series or p["ts"] > series[-1]["ts"]
    ]
    return series


def get_series(
    namespace: str, uuid: str, target_points: int | None = None
) -> dict | None:
    if not enabled():
        return None
    doc = _read_doc(namespace, uuid)
    if not doc or doc.get("metrics_available") is False or "accumulated" not in doc:
        return None

    cfg = papiconf.MAIN_CONF["energy"]
    if target_points is None:
        target_points = int(cfg.get("deployment_series_points", 1000))
    step_s = int(cfg["series_bucket_seconds"])
    series = _build_series(
        list(store.read_series(namespace, uuid, None, None))
        + list(doc.get("tail") or []),
        live_series(doc, bucket_s=step_s),
        target_points,
    )
    stats = _apply_topup(_to_stats(doc), doc)

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


def _bucket(b: dict) -> compute.Bucket:
    return compute.Bucket(
        ts=b["ts"],
        energy_wh=b.get("energy_wh", 0.0),
        power_w_avg=b.get("power_w_avg", 0.0),
        carbon_g=b.get("carbon_g", 0.0),
        water_l=b.get("water_l", 0.0),
    )


def _point(b: compute.Bucket, live: bool) -> dict:
    return {
        "ts": b.ts,
        "power_w": b.power_w_avg,
        "energy_wh": b.energy_wh,
        "carbon_g": b.carbon_g,
        "water_l": b.water_l,
        "live": live,
    }


def _user_series(namespace: str, rows: list, target_points: int) -> list[dict]:
    """
    One time series for a set of the user's deployments. The raw 15-min buckets
    of every deployment (persisted archive + 24h tail) are merged by timestamp
    first, then downsampled once: uniform resolution across the whole series and
    the same hard `target_points` cap as the other levels, regardless of how long
    each deployment individually has been running. The live Mimir tails are then
    merged and appended un-downsampled (as `get_series` / `_cluster_series` do).
    """
    step_s = int(papiconf.MAIN_CONF["energy"]["series_bucket_seconds"])

    persisted = [
        [
            _bucket(b)
            for b in list(store.read_series(namespace, uuid, None, None))
            + list(doc.get("tail") or [])
        ]
        for uuid, doc, _ in rows
    ]
    merged = sorted(compute.merge_buckets(persisted), key=lambda b: b.ts)
    series = [_point(b, live=False) for b in compute.downsample(merged, target_points)]

    live = sorted(
        compute.merge_buckets(
            [
                [_bucket(b) for b in live_series(doc, bucket_s=step_s)]
                for _uuid, doc, _ in rows
            ]
        ),
        key=lambda b: b.ts,
    )
    series += [
        _point(b, live=True) for b in live if not series or b.ts > series[-1]["ts"]
    ]
    return series
