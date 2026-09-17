"""
Background sweep + live top-up for the energy-accounting feature.

`run_sweep()` runs on a `@repeat_every` thread in `ai4papi.main`. Per deployment
(and per datacenter for `/stats/cluster`) it integrates the un-consolidated
window of Mimir power into energy buckets (`energy.series_bucket_seconds`,
15 min), applies the per-datacenter TUE and the Wattnet footprint, keeps a
running accumulator plus a ~24h "tail" that is re-computed every sweep (so the
footprint self-heals as Wattnet settles), and persists a small JSON document.

`topup(doc)` is called on the read path: it adds the `pointer_ts -> now` slice
live from Mimir so the dashboard counter moves every ~30s.
"""

import datetime
import logging

from cachetools import TTLCache, cached

import ai4papi.conf as papiconf
from ai4papi import mimir
from ai4papi.accounting import compute, store
from ai4papi.nomad_utils import Nomad
from ai4papi.wattnet import green_director

LOG = logging.getLogger(__name__)

_JOB_PREFIXES = ("module", "tool", "batch", "try")
_KEYS = ("energy_wh", "carbon_g", "water_l")  # TUE-normalized totals kept per accum
_EMPTY = dict.fromkeys(_KEYS, 0.0)


def _cfg() -> dict:
    return papiconf.MAIN_CONF["energy"]


def enabled() -> bool:
    return (
        bool(_cfg().get("enabled"))
        and store.base_dir() is not None
        and bool(mimir.MIMIR_USER)
    )


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _dt(ts: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def _floor(ts: float, step: int) -> float:
    return (int(ts) // step) * step


def _earliest_bucket_ts(ns: str, uuid: str, doc: dict) -> float | None:
    """Timestamp of the oldest 15-min bucket on record: the consolidated
    `.series.jsonl` if there is one, else the oldest still in `doc["tail"]`."""
    archive = store.read_series(ns, uuid, None, None)
    if archive:
        return compute._parse_iso(archive[0]["ts"])
    tail = doc.get("tail") or []
    if tail:
        return min(compute._parse_iso(b["ts"]) for b in tail)
    return None


def _window_start(
    ns: str,
    uuid: str,
    doc: dict,
    settled_ts: float,
    retention_cut: float,
    bucket_s: int,
) -> float:
    """
    True start of the accumulation window, exposed to the API as `since`.

    `settled_ts` cannot serve this role: it marches forward every sweep as
    buckets age into `doc["settled"]`, while the accumulated totals (and the
    persisted series) keep every bucket since the beginning.

    Pinned once and only moved forward if it falls behind Mimir retention:
    - fresh doc: the first-sweep `settled_ts` is the window start;
    - doc written before this field existed (`settled` / `tail` already there,
      `settled_ts` already advanced): recover it from the oldest bucket on record.
    """
    retention_floor = _floor(retention_cut, bucket_s)
    if doc.get("window_start"):
        return max(compute._parse_iso(doc["window_start"]), retention_floor)
    if "settled" in doc or doc.get("tail"):
        earliest = _earliest_bucket_ts(ns, uuid, doc)
        if earliest is not None:
            return max(min(earliest, settled_ts), retention_floor)
    return max(settled_ts, retention_floor)


def _tue(dc: str) -> float:
    return (papiconf.datacenters.get(dc) or {}).get("TUE") or _cfg()[
        "default_tue_factor"
    ]


def _sum(buckets) -> dict:
    return {k: sum(b[k] for b in buckets) for k in _KEYS}


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in _KEYS}


def _footprint_series(dc: str, start_ts: float, end_ts: float):
    """
    (carbon, water) intensity series for a datacenter over [start, end].
    In steady state uses the 7-day window that `green_director` already keeps;
    only fetches a wider range from Wattnet when backfilling further back.
    `apply_footprint` falls back to DEFAULTS when a series is empty/None.
    """
    metrics = green_director.metrics.get(dc, {})
    if start_ts >= _now().timestamp() - 7 * 86400:
        return metrics.get("carbon"), metrics.get("water")
    try:
        fetched = green_director.footprint_series(dc, _dt(start_ts), _dt(end_ts))
    except Exception:
        LOG.warning("wattnet footprint_series failed for %s", dc)
        fetched = {}
    return (
        fetched.get("carbon") or metrics.get("carbon"),
        fetched.get("water") or metrics.get("water"),
    )


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def run_sweep() -> None:
    if not enabled():
        return
    now = _now()
    for ns in set(papiconf.MAIN_CONF["nomad"]["namespaces"].values()):
        try:
            _sweep_namespace(ns, now)
        except Exception:
            LOG.exception("energy sweep failed for namespace %s", ns)
    try:
        _sweep_cluster(now)
    except Exception:
        LOG.exception("cluster energy sweep failed")


def _info_from_job(j: dict, ns: str) -> dict:
    """`info` dict `process_single` expects, from a raw Nomad job."""
    meta = j.get("Meta") or {}
    return {
        "uuid": j["ID"],
        "job_name": j["Name"],
        "namespace": ns,
        "owner": meta.get("owner", ""),
        "submit_time": (j["SubmitTime"] / 1e9) if j.get("SubmitTime") else None,
        "status": "running",
    }


def _sweep_namespace(ns: str, now: datetime.datetime) -> None:
    try:
        jobs = Nomad.jobs.get_jobs(namespace=ns, filter_='Status != "dead"')
    except Exception:
        LOG.exception("could not list jobs for namespace %s", ns)
        return

    seen = set()
    for stub in jobs:
        if not stub["Name"].startswith(_JOB_PREFIXES):
            continue
        seen.add(stub["ID"])
        # the job list stub does not carry Meta, fetch the full job for `owner`
        try:
            j = Nomad.job.get_job(id_=stub["ID"], namespace=ns)
        except Exception:
            LOG.exception("could not fetch job %s", stub["ID"])
            continue
        try:
            process_single(_info_from_job(j, ns), now)
        except Exception:
            LOG.exception("energy sweep failed for deployment %s", stub["ID"])

    # close deployments whose Nomad job has been garbage-collected
    for uuid, doc in store.iter_namespace(ns):
        if uuid in seen or doc.get("closed_at"):
            continue
        info = {
            "uuid": uuid,
            "job_name": doc.get("job_name"),
            "namespace": ns,
            "owner": doc.get("owner", ""),
            "submit_time": doc.get("submit_time_epoch"),
            "status": "closed",
        }
        try:
            process_single(info, now, closing=True)
        except Exception:
            LOG.exception("energy close failed for deployment %s", uuid)


def ensure_swept(ns: str, uuid: str) -> dict | None:
    """
    On-demand equivalent of one sweep pass for a single deployment, called from
    the read path when no accumulator doc exists yet: a deployment that already
    has real samples in Mimir does not have to wait up to `sweep_seconds` for
    the background thread to reach it before the API can report anything.

    A no-op (returns the existing doc untouched, no Nomad/Mimir call) once the
    background sweep has processed this deployment at least once, so it never
    duplicates the periodic sweep's work. `None` if the job cannot be found (not
    a deployment, or already gone).
    """
    if not enabled():
        return None
    doc = store.read_accum(ns, uuid)
    if doc is not None:
        return doc
    try:
        j = Nomad.job.get_job(id_=uuid, namespace=ns)
    except Exception:
        return None
    try:
        process_single(_info_from_job(j, ns), _now())
    except Exception:
        LOG.warning("on-demand energy sweep failed for %s/%s", ns, uuid, exc_info=True)
        return None
    return store.read_accum(ns, uuid)


_bulk_sweep_cache: TTLCache = TTLCache(
    maxsize=2048, ttl=papiconf.MAIN_CONF["energy"].get("topup_cache_seconds", 25)
)


@cached(_bulk_sweep_cache)
def ensure_swept_bulk(ns: str, owner: str) -> list[dict]:
    """
    On-demand equivalent of one sweep pass, restricted to one owner's active
    deployments in `ns`: the bulk counterpart of `ensure_swept`, used by the
    list (`get_accumulated_bulk`) and per-user (`get_user_energy`) read paths so
    a just-deployed job shows up there too, not only on its own detail endpoint.

    Lists the owner's active jobs (cheap, no per-job fetch) and only runs
    `process_single` for the ones with no accumulator doc yet; a no-op once the
    background sweep (or a prior on-demand call) has reached all of them.
    Cached for `topup_cache_seconds`: without it, every list/`/stats/user` read
    would list Nomad on every single request, forever, not just during the
    bootstrap window -- a few seconds of staleness for a listing that itself
    only matters for brand-new deployments is a good trade.

    Returns the filtered job stubs (`[{"ID": ..., "Name": ...}, ...]`) so the
    caller can also try `live_only_doc` for whichever of them still have no doc
    afterwards (a deployment too young for even one completed bucket), without
    listing Nomad a second time. `[]` when disabled or the listing itself fails.
    """
    if not enabled():
        return []
    try:
        jobs = Nomad.jobs.get_jobs(
            namespace=ns,
            filter_=f'Meta.owner == "{owner}" and Status != "dead"',
        )
    except Exception:
        LOG.warning("could not list jobs for on-demand sweep (%s/%s)", ns, owner)
        return []
    now = _now()
    stubs = [s for s in jobs if s["Name"].startswith(_JOB_PREFIXES)]
    for stub in stubs:
        if store.read_accum(ns, stub["ID"]) is not None:
            continue
        try:
            j = Nomad.job.get_job(id_=stub["ID"], namespace=ns)
            process_single(_info_from_job(j, ns), now)
        except Exception:
            LOG.warning(
                "on-demand energy sweep failed for %s/%s", ns, stub["ID"], exc_info=True
            )
    return stubs


_live_only_doc_cache: TTLCache = TTLCache(
    maxsize=8192, ttl=papiconf.MAIN_CONF["energy"].get("topup_cache_seconds", 25)
)


@cached(_live_only_doc_cache)
def live_only_doc(ns: str, uuid: str) -> dict | None:
    """
    Transient stand-in for a deployment `process_single` has not written any doc
    for at all yet -- typically younger than one `series_bucket_seconds`, so it
    has no completed bucket to fold into an accumulator (see `ensure_swept`,
    which still leaves such a deployment with no doc by design: there is
    nothing settled to persist). Never written to the store.

    Cached for `topup_cache_seconds`: without it, every read of a deployment
    still in this bootstrap window would fetch its Nomad job and resolve its
    allocations from scratch on every single request for as long as it stays
    doc-less (which can be several minutes). A few seconds of staleness on
    which allocation is "current" is a good trade against that.

    Shaped so the normal `topup` / `live_series` machinery -- and therefore
    `accounting._to_stats` / `_apply_topup` / `_build_series` -- treats it like
    a real doc with an all-zero accumulator and `pointer_ts = submit_time`: the
    whole reported number then comes from the live top-up's Mimir query, capped
    at the same lookback `topup` itself uses. `None` if the job cannot be
    resolved to a running, monitored allocation (not found, queued, or no
    allocation yet).
    """
    if not enabled():
        return None
    try:
        j = Nomad.job.get_job(id_=uuid, namespace=ns)
    except Exception:
        return None
    info = _info_from_job(j, ns)
    try:
        allocs_by_dc, _windows, running_allocs, _partial = compute.resolve_alloc_ids(
            uuid, ns
        )
    except Exception:
        return None
    if not running_allocs:
        return None
    submit = info.get("submit_time")
    submit_ts = submit or _now().timestamp()
    # `window_start` below is pinned to the exact submit time, same as
    # `process_single`'s first-encounter `window_start` -- not started late or
    # clamped, so `complete` is `True` unless we had no real `submit_time` to
    # pin it to at all (job fetched with no `SubmitTime`, an edge case) or it
    # somehow predates the lookback window (impossible in practice: this path
    # only ever runs for a deployment too young to have a doc yet).
    complete = bool(submit) and submit >= _now().timestamp() - _cfg()[
        "initial_lookback_hours"
    ] * 3600
    return {
        "deployment_uuid": uuid,
        "namespace": ns,
        "job_name": info.get("job_name"),
        "owner": info.get("owner"),
        "status": "running",
        "metrics_available": True,
        "complete": complete,
        "window_start": compute._iso(submit_ts),
        "settled_ts": compute._iso(submit_ts),
        "pointer_ts": compute._iso(submit_ts),
        "active_allocs": running_allocs,
        "datacenters": sorted(allocs_by_dc),
        "tail": [],
        "settled": dict(_EMPTY),
        "accumulated": dict(_EMPTY),
    }


def process_single(info: dict, now: datetime.datetime, closing: bool = False) -> None:
    cfg = _cfg()
    ns, uuid = info["namespace"], info["uuid"]
    bucket_s = int(cfg["series_bucket_seconds"])
    step_s = int(cfg["query_step_seconds"])
    now_ts = now.timestamp()
    revision_cut = now_ts - cfg["footprint_revision_hours"] * 3600
    retention_cut = now_ts - cfg["mimir_retention_days"] * 86400
    lookback_ts = now_ts - cfg["initial_lookback_hours"] * 3600

    doc = store.read_accum(ns, uuid) or {}
    doc.pop("partial", None)  # dropped in favour of `coverage_ratio` (always a float)

    # integration pointer: on first encounter never look back further than
    # `initial_lookback_hours` (pre-existing deployments are not fully backfilled).
    if doc.get("settled_ts"):
        settled_ts = compute._parse_iso(doc["settled_ts"])
        complete = doc.get("complete", True)
    else:
        submit = info.get("submit_time")
        settled_ts = max(submit or lookback_ts, lookback_ts)
        complete = bool(submit) and submit >= lookback_ts
    if settled_ts < retention_cut:
        settled_ts, complete = retention_cut, False
    settled_ts = _floor(settled_ts, bucket_s)
    window_start = _window_start(ns, uuid, doc, settled_ts, retention_cut, bucket_s)
    extract_to = _floor(now_ts - cfg["query_lag_seconds"], bucket_s)

    try:
        allocs_by_dc, alloc_windows, running_allocs, partial_dc = (
            compute.resolve_alloc_ids(uuid, ns)
        )
    except Exception:
        allocs_by_dc, alloc_windows, running_allocs, partial_dc = {}, [], [], False

    # metadata (always refreshed)
    doc.update(
        {
            "deployment_uuid": uuid,
            "job_name": info.get("job_name") or doc.get("job_name"),
            "namespace": ns,
            "owner": info.get("owner") or doc.get("owner", ""),
            "status": "closed" if closing else info.get("status", "running"),
            "complete": complete,
            "updated_at": compute._iso(now_ts),
            "active_allocs": running_allocs,
        }
    )
    if info.get("submit_time"):
        doc["submit_time"] = compute._iso(info["submit_time"])
        doc["submit_time_epoch"] = info["submit_time"]
    if allocs_by_dc:
        doc["datacenters"] = list(allocs_by_dc)
    if closing and not doc.get("closed_at"):
        doc["closed_at"] = compute._iso(now_ts)

    run_s = compute.running_seconds(alloc_windows, settled_ts, extract_to)

    # no monitored allocations: queued, or ran only in unmonitored sites
    if not allocs_by_dc:
        if "settled" not in doc and partial_dc and run_s > 2 * bucket_s:
            doc.update({"metrics_available": False, "coverage_ratio": 0.0})
        store.write_accum(ns, uuid, doc)
        return

    # integrate [settled_ts, extract_to] per datacenter
    dq = cfg.get("data_quality")
    new_buckets = []
    if settled_ts < extract_to:
        span = extract_to - settled_ts
        qfn = (
            mimir.query_range_chunked
            if span > mimir.MAX_POINTS_PER_QUERY * step_s
            else mimir.query_range
        )
        start_dt, end_dt = _dt(settled_ts), _dt(extract_to)
        per_dc = []
        tue_norm = tue_raw = 0.0  # energy-weighted mean TUE over the new buckets
        for dc, alloc_ids in allocs_by_dc.items():
            cpu_q, gpu_q = compute.power_promql(alloc_ids)
            # CPU and GPU are treated symmetrically: a missing metric already
            # comes back as an empty result, and a transient query error on one
            # source must not discard the other (a GPU-only monitored site has
            # no `scaph_*` series at all).
            try:
                cpu = qfn(cpu_q, start_dt, end_dt, step_s)
            except mimir.MimirError:
                LOG.warning("mimir CPU query failed for %s/%s", ns, uuid)
                cpu = []
            try:
                gpu = qfn(gpu_q, start_dt, end_dt, step_s)
            except mimir.MimirError:
                LOG.warning("mimir GPU query failed for %s/%s", ns, uuid)
                gpu = []
            if not cpu and not gpu:
                continue
            tue = _tue(dc)
            buckets = compute.integrate(
                cpu, gpu, step_s, bucket_s, tue, data_quality=dq
            )
            if buckets:
                e = sum(b.energy_wh for b in buckets)
                tue_norm += e
                tue_raw += e / tue
                carbon_s, water_s = _footprint_series(dc, settled_ts, now_ts)
                buckets = compute.apply_footprint(buckets, carbon_s, water_s)
            per_dc.append(buckets)
        if tue_raw:
            doc["tue_factor"] = tue_norm / tue_raw
        elif "tue_factor" not in doc and allocs_by_dc:
            doc["tue_factor"] = _tue(next(iter(allocs_by_dc)))
        new_buckets = [
            b
            for b in compute.merge_buckets(per_dc)
            if compute._parse_iso(b.ts) + bucket_s <= extract_to
        ]
        # clip to the deployment's actual life: drop buckets with no overlap with
        # any allocation window. Guards against metric staleness (the per-alloc
        # power series and `nomad_gpu_allocation_info` can linger a few minutes
        # after an allocation ends, which would otherwise attribute idle GPU /
        # host power to a finished deployment).
        if alloc_windows:
            new_buckets = [
                b
                for b in new_buckets
                if compute.running_seconds(
                    alloc_windows,
                    compute._parse_iso(b.ts),
                    compute._parse_iso(b.ts) + bucket_s,
                )
                > 0
            ]

    # best-effort coverage: no data while the deployment was clearly running
    # means the node has no monitoring stack yet -> null, retry next sweep.
    if not new_buckets and "settled" not in doc:
        if run_s > 2 * bucket_s:
            doc.update({"metrics_available": False, "coverage_ratio": 0.0})
            store.write_accum(ns, uuid, doc)
        return

    doc["metrics_available"] = True
    doc["window_start"] = compute._iso(window_start)

    aged_n, tail_n = _fold_and_accumulate(
        ns, uuid, doc, new_buckets, settled_ts, revision_cut, bucket_s
    )

    # coverage: fraction of the run time (including time on unmonitored sites, so
    # `partial_dc` is captured implicitly) that ended up with power data.
    covered_s = (tail_n + aged_n) * bucket_s
    if run_s > 0:
        doc["coverage_ratio"] = round(min(1.0, covered_s / run_s), 3)
    else:
        doc["coverage_ratio"] = 1.0

    store.write_accum(ns, uuid, doc)


def _fold_and_accumulate(
    ns: str,
    uuid: str,
    doc: dict,
    new_buckets: list,
    settled_ts: float,
    revision_cut: float,
    bucket_s: int,
) -> tuple[int, int]:
    """
    Merge `new_buckets` (Bucket namedtuples) over `doc["tail"]` keyed by `ts`,
    age the buckets older than `revision_cut` into `doc["settled"]` (appending
    them to the persisted series), and recompute `settled_ts` / `accumulated` /
    `pointer_ts` on `doc`. Returns `(aged_count, tail_count)`.

    Shared by the per-deployment (`process_single`) and per-datacenter
    (`process_datacenter`) sweeps.
    """
    settled = dict(doc.get("settled") or _EMPTY)

    by_ts = {b["ts"]: b for b in (doc.get("tail") or [])}
    for b in new_buckets:
        by_ts[b.ts] = b._asdict()

    tail, aged_out = [], []
    for ts in sorted(by_ts):
        (aged_out if compute._parse_iso(ts) < revision_cut else tail).append(by_ts[ts])

    if aged_out:
        settled = _add(settled, _sum(aged_out))
        store.append_series(ns, uuid, aged_out)
        settled_ts = max(compute._parse_iso(b["ts"]) for b in aged_out) + bucket_s

    doc["settled"] = settled
    doc["tail"] = tail
    doc["settled_ts"] = compute._iso(settled_ts)
    doc["accumulated"] = _add(settled, _sum(tail))
    doc["pointer_ts"] = compute._iso(
        (compute._parse_iso(tail[-1]["ts"]) + bucket_s) if tail else settled_ts
    )
    return len(aged_out), len(tail)


# ---------------------------------------------------------------------------
# per-datacenter sweep (cluster-level energy accumulator)
# ---------------------------------------------------------------------------

# Pseudo-namespace for the datacenter-level documents. Does not collide with any
# VO namespace nor with the deployment sweep's namespace loop.
CLUSTER_NS = "_cluster"


def _cluster_promql(dc: str) -> tuple[str, str]:
    """
    (host_query, gpu_query) for a datacenter: one series per host and one per
    GPU (grouped, not summed) so `compute.integrate` runs the data-quality
    filter on each raw Mimir series before summing the energy.
    """
    host_q = f'sum by (node) (scaph_host_power_microwatts{{datacenter="{dc}"}})'
    gpu_q = f'sum by (UUID) (DCGM_FI_DEV_POWER_USAGE{{datacenter="{dc}"}})'
    return host_q, gpu_q


def _cluster_dq() -> dict:
    """
    Data-quality config for the datacenter sweep: reuse `energy.data_quality`
    but drop the absolute `max_power_w` ceiling (it is calibrated per allocation
    / per GPU, far below a whole host). Negative-drop and the scale-free spike
    check still apply, per host / per GPU series.
    """
    return {**(_cfg().get("data_quality") or {}), "max_power_w": 0}


def _sweep_cluster(now: datetime.datetime) -> None:
    for dc in _cfg()["metrics_datacenters"]:
        try:
            process_datacenter(dc, now)
        except Exception:
            LOG.exception("cluster energy sweep failed for datacenter %s", dc)


def process_datacenter(dc: str, now: datetime.datetime) -> None:
    cfg = _cfg()
    bucket_s = int(cfg["series_bucket_seconds"])
    step_s = int(cfg["query_step_seconds"])
    now_ts = now.timestamp()
    revision_cut = now_ts - cfg["footprint_revision_hours"] * 3600
    retention_cut = now_ts - cfg["mimir_retention_days"] * 86400
    lookback_ts = now_ts - cfg["initial_lookback_hours"] * 3600

    doc = store.read_accum(CLUSTER_NS, dc) or {}

    if doc.get("settled_ts"):
        settled_ts = compute._parse_iso(doc["settled_ts"])
        complete = doc.get("complete", True)
    else:
        settled_ts, complete = lookback_ts, False
    if settled_ts < retention_cut:
        settled_ts, complete = retention_cut, False
    settled_ts = _floor(settled_ts, bucket_s)
    window_start = _window_start(
        CLUSTER_NS, dc, doc, settled_ts, retention_cut, bucket_s
    )
    extract_to = _floor(now_ts - cfg["query_lag_seconds"], bucket_s)

    doc.update(
        {
            "datacenter": dc,
            "deployment_uuid": dc,  # reuse the same key name as deployment docs
            "namespace": CLUSTER_NS,
            "complete": complete,
            "updated_at": compute._iso(now_ts),
            "tue_factor": _tue(dc),
        }
    )

    new_buckets = []
    if settled_ts < extract_to:
        span = extract_to - settled_ts
        qfn = (
            mimir.query_range_chunked
            if span > mimir.MAX_POINTS_PER_QUERY * step_s
            else mimir.query_range
        )
        host_q, gpu_q = _cluster_promql(dc)
        start_dt, end_dt = _dt(settled_ts), _dt(extract_to)
        try:
            host = qfn(host_q, start_dt, end_dt, step_s)
        except mimir.MimirError:
            LOG.warning("mimir host-power query failed for %s", dc)
            host = []
        try:
            gpu = qfn(gpu_q, start_dt, end_dt, step_s)
        except mimir.MimirError:
            gpu = []
        buckets = compute.integrate(
            host, gpu, step_s, bucket_s, _tue(dc), data_quality=_cluster_dq()
        )
        if buckets:
            carbon_s, water_s = _footprint_series(dc, settled_ts, now_ts)
            buckets = compute.apply_footprint(buckets, carbon_s, water_s)
        new_buckets = [
            b for b in buckets if compute._parse_iso(b.ts) + bucket_s <= extract_to
        ]

    if not new_buckets and "settled" not in doc:
        # datacenter has no host-power series yet: retry next sweep, no zeros
        doc["metrics_available"] = False
        store.write_accum(CLUSTER_NS, dc, doc)
        return

    doc["metrics_available"] = True
    doc["window_start"] = compute._iso(window_start)
    _fold_and_accumulate(
        CLUSTER_NS, dc, doc, new_buckets, settled_ts, revision_cut, bucket_s
    )
    store.write_accum(CLUSTER_NS, dc, doc)


# ---------------------------------------------------------------------------
# live path: `pointer_ts -> now` slice from Mimir (read side)
# ---------------------------------------------------------------------------

_live_cache: TTLCache = TTLCache(
    maxsize=8192, ttl=papiconf.MAIN_CONF["energy"].get("topup_cache_seconds", 25)
)


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
    cfg = _cfg()
    end_ts = _now().timestamp()
    start_ts = max(
        compute._parse_iso(pointer_iso),
        end_ts - (cfg["sweep_seconds"] + 4 * cfg["series_bucket_seconds"]),
    )
    return start_ts, end_ts


def _running_key(doc: dict) -> tuple | None:
    pointer = doc.get("pointer_ts") or doc.get("settled_ts")
    running = doc.get("active_allocs") or []
    if not doc.get("metrics_available", False) or not pointer or not running:
        return None
    return (
        doc["namespace"],
        doc["deployment_uuid"],
        pointer,
        tuple(sorted((a["alloc_id"], a["datacenter"]) for a in running)),
    )


def _live_buckets(alloc_pairs, start_ts: float, end_ts: float, bucket_s: int):
    """
    Integrate the live window from Mimir into `bucket_s` buckets (per DC, merged),
    plus the current draw from the last sample of that same query. Returns
    `(buckets, power_w)`; `power_w` is `None` only when a series has no sample
    at all in the window (nothing to read yet), never because the last sample
    is "too old" -- Mimir data is immutable once ingested, so the actual last
    point is always the right one to show, however old it is.
    """
    cfg = _cfg()
    step_s = int(cfg["query_step_seconds"])
    if end_ts - start_ts < step_s:
        return [], None

    by_dc: dict[str, list[str]] = {}
    for alloc_id, dc in alloc_pairs:
        by_dc.setdefault(dc, []).append(alloc_id)

    start_dt, end_dt = _dt(start_ts), _dt(end_ts)
    per_dc = []
    inst_w = 0.0
    any_inst = False
    for dc, alloc_ids in by_dc.items():
        cpu_q, gpu_q = compute.power_promql(alloc_ids)
        try:
            cpu = mimir.query_range(cpu_q, start_dt, end_dt, step_s)
        except mimir.MimirError:
            cpu = []
        try:
            gpu = mimir.query_range(gpu_q, start_dt, end_dt, step_s)
        except mimir.MimirError:
            gpu = []
        dq = cfg.get("data_quality")
        buckets = compute.integrate(
            cpu, gpu, step_s, bucket_s, _tue(dc), data_quality=dq
        )
        metrics = green_director.metrics.get(dc, {})
        per_dc.append(
            compute.apply_footprint(
                buckets, metrics.get("carbon"), metrics.get("water")
            )
        )
        iw = compute.instant_power(cpu, gpu, _tue(dc), dq)
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
            "energy top-up failed for %s", doc.get("deployment_uuid"), exc_info=True
        )
        return {"degraded": True}


@cached(_live_cache)
def _compute_topup(key) -> dict:
    _ns, _uuid, pointer_iso, alloc_pairs = key
    start_ts, end_ts = _live_window(pointer_iso)
    result = {
        **_EMPTY,
        "power_w": None,
        "live_as_of": compute._iso(end_ts),
        "degraded": False,
    }
    span = int(end_ts - start_ts) + int(_cfg()["query_step_seconds"])
    buckets, inst_w = _live_buckets(
        alloc_pairs, start_ts, end_ts, bucket_s=max(span, 1)
    )
    for b in buckets:
        for k in _KEYS:
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
    _ns, _uuid, pointer_iso, alloc_pairs = key
    start_ts, end_ts = _live_window(pointer_iso)
    try:
        buckets, _ = _live_buckets(
            alloc_pairs, start_ts, end_ts, bucket_s=max(bucket_s, 1)
        )
    except Exception:
        LOG.warning("energy live_series failed for %s", doc.get("deployment_uuid"))
        return []
    return [{**b._asdict(), "live": True} for b in buckets]


# ---------------------------------------------------------------------------
# live path for the per-datacenter (cluster) accumulator
# ---------------------------------------------------------------------------


def _cluster_live_buckets(dc: str, start_ts: float, end_ts: float, bucket_s: int):
    """Integrate the live window from Mimir for one datacenter into buckets."""
    cfg = _cfg()
    step_s = int(cfg["query_step_seconds"])
    if end_ts - start_ts < step_s:
        return []
    host_q, gpu_q = _cluster_promql(dc)
    start_dt, end_dt = _dt(start_ts), _dt(end_ts)
    host = mimir.query_range(host_q, start_dt, end_dt, step_s)
    try:
        gpu = mimir.query_range(gpu_q, start_dt, end_dt, step_s)
    except mimir.MimirError:
        gpu = []
    buckets = compute.integrate(
        host, gpu, step_s, bucket_s, _tue(dc), data_quality=_cluster_dq()
    )
    metrics = green_director.metrics.get(dc, {})
    return compute.apply_footprint(buckets, metrics.get("carbon"), metrics.get("water"))


def _cluster_pointer(doc: dict | None) -> str | None:
    if not doc or not doc.get("metrics_available", False):
        return None
    return doc.get("pointer_ts") or doc.get("settled_ts")


def topup_cluster(dc: str, doc: dict | None) -> dict | None:
    """Live `pointer_ts -> now` slice for a datacenter (see `topup`)."""
    pointer = _cluster_pointer(doc)
    if pointer is None:
        return None
    try:
        return _compute_cluster_topup((CLUSTER_NS, dc, pointer))
    except Exception:
        LOG.warning("cluster energy top-up failed for %s", dc, exc_info=True)
        return {"degraded": True}


@cached(_live_cache)
def _compute_cluster_topup(key) -> dict:
    # Only the energy deltas + `live_as_of` are consumed (the datacenter `power_w`
    # comes from `get_datacenter_power`'s instant query, not from here).
    _ns, dc, pointer_iso = key
    start_ts, end_ts = _live_window(pointer_iso)
    result = {**_EMPTY, "live_as_of": compute._iso(end_ts), "degraded": False}
    span = int(end_ts - start_ts) + int(_cfg()["query_step_seconds"])
    for b in _cluster_live_buckets(dc, start_ts, end_ts, bucket_s=max(span, 1)):
        for k in _KEYS:
            result[k] += getattr(b, k)
    return result


def live_series_cluster(dc: str, doc: dict | None, bucket_s: int) -> list[dict]:
    """Live tail for a datacenter as time-series points (see `live_series`)."""
    pointer = _cluster_pointer(doc)
    if pointer is None:
        return []
    start_ts, end_ts = _live_window(pointer)
    try:
        buckets = _cluster_live_buckets(dc, start_ts, end_ts, bucket_s=max(bucket_s, 1))
    except Exception:
        LOG.warning("cluster live_series failed for %s", dc)
        return []
    return [{**b._asdict(), "live": True} for b in buckets]
