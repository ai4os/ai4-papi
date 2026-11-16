"""
Background sweep of the energy-accounting feature (write path).

`run_sweep()` runs on a `@repeat_every` thread in `ai4papi.main`. Per deployment
(and per datacenter for `/stats/cluster`) it integrates the un-consolidated
window of Mimir power into energy buckets (`energy.series_bucket_seconds`,
15 min), applies the per-datacenter TUE and the Wattnet footprint, keeps a
running accumulator plus a ~24h "tail" that is re-computed every sweep (so the
footprint self-heals as Wattnet settles), and persists a small JSON document.

The read-side counterparts live in `ai4papi.energy.live` (live top-up) and
`ai4papi.energy.ondemand` (sweep a deployment from the read path).
"""

import logging
from typing import NamedTuple

import ai4papi.conf as papiconf
from ai4papi.clients import mimir
from ai4papi.energy import compute, config, query, store
from ai4papi.nomad_utils import Nomad
from ai4papi.wattnet import green_director

LOG = logging.getLogger(__name__)


class Clock(NamedTuple):
    """Time references of one sweep pass (all epoch seconds unless noted)."""

    now_ts: float
    bucket_s: int
    step_s: int
    extract_to: float  # newest instant to integrate (now minus the ingestion lag)
    revision_cut: float  # buckets older than this are final (footprint settled)
    retention_cut: float  # oldest instant Mimir still holds
    lookback_ts: float  # how far back a first-seen deployment is integrated


def _clock(now) -> Clock:
    cfg = config.get()
    now_ts = now.timestamp()
    bucket_s = int(cfg["series_bucket_seconds"])
    return Clock(
        now_ts=now_ts,
        bucket_s=bucket_s,
        step_s=int(cfg["query_step_seconds"]),
        extract_to=config.floor(now_ts - cfg["query_lag_seconds"], bucket_s),
        revision_cut=now_ts - cfg["footprint_revision_hours"] * 3600,
        retention_cut=now_ts - cfg["mimir_retention_days"] * 86400,
        lookback_ts=now_ts - cfg["initial_lookback_hours"] * 3600,
    )


def _resume_point(doc: dict, t: Clock, submit: float | None = None):
    """
    `(settled_ts, complete)`: where integration resumes. On first encounter never
    look back further than `initial_lookback_hours` (pre-existing deployments are
    not fully backfilled); never further than Mimir retention either.
    """
    if doc.get("settled_ts"):
        settled_ts = compute.parse_iso(doc["settled_ts"])
        complete = doc.get("complete", True)
    else:
        settled_ts = max(submit or t.lookback_ts, t.lookback_ts)
        complete = bool(submit) and submit >= t.lookback_ts
    if settled_ts < t.retention_cut:
        settled_ts, complete = t.retention_cut, False
    return config.floor(settled_ts, t.bucket_s), complete


def _earliest_bucket_ts(ns: str, uuid: str, doc: dict) -> float | None:
    """Timestamp of the oldest 15-min bucket on record: the consolidated
    `.series.jsonl` if there is one, else the oldest still in `doc["tail"]`."""
    archive = store.read_series(ns, uuid, None, None)
    if archive:
        return compute.parse_iso(archive[0]["ts"])
    tail = doc.get("tail") or []
    if tail:
        return min(compute.parse_iso(b["ts"]) for b in tail)
    return None


def _window_start(ns: str, uuid: str, doc: dict, settled_ts: float, t: Clock) -> float:
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
    retention_floor = config.floor(t.retention_cut, t.bucket_s)
    if doc.get("window_start"):
        return max(compute.parse_iso(doc["window_start"]), retention_floor)
    if "settled" in doc or doc.get("tail"):
        earliest = _earliest_bucket_ts(ns, uuid, doc)
        if earliest is not None:
            return max(min(earliest, settled_ts), retention_floor)
    return max(settled_ts, retention_floor)


def _footprint_series(dc: str, start_ts: float, end_ts: float):
    """
    (carbon, water) intensity series for a datacenter over [start, end].
    In steady state uses the 7-day window that `green_director` already keeps;
    only fetches a wider range from Wattnet when backfilling further back.
    `apply_footprint` falls back to DEFAULTS when a series is empty/None.
    """
    metrics = green_director.metrics.get(dc, {})
    if start_ts >= config.now().timestamp() - 7 * 86400:
        return metrics.get("carbon"), metrics.get("water")
    try:
        fetched = green_director.footprint_series(
            dc, config.dt(start_ts), config.dt(end_ts)
        )
    except Exception:
        LOG.warning("wattnet footprint_series failed for %s", dc)
        fetched = {}
    return (
        fetched.get("carbon") or metrics.get("carbon"),
        fetched.get("water") or metrics.get("water"),
    )


def _with_footprint(dc: str, buckets: list, settled_ts: float, t: Clock) -> list:
    if not buckets:
        return buckets
    carbon, water = _footprint_series(dc, settled_ts, t.now_ts)
    return compute.apply_footprint(buckets, carbon, water)


def _complete_buckets(buckets, t: Clock) -> list:
    """Only buckets that are fully inside the integrated window."""
    return [b for b in buckets if compute.parse_iso(b.ts) + t.bucket_s <= t.extract_to]


# ---------------------------------------------------------------------------
# sweep driver
# ---------------------------------------------------------------------------


def run_sweep() -> None:
    if not config.enabled():
        return
    now = config.now()
    for ns in set(papiconf.MAIN_CONF["nomad"]["namespaces"].values()):
        try:
            _sweep_namespace(ns, now)
        except Exception:
            LOG.exception("energy sweep failed for namespace %s", ns)
    try:
        _sweep_cluster(now)
    except Exception:
        LOG.exception("cluster energy sweep failed")


def info_from_job(j: dict, ns: str) -> dict:
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


def _sweep_namespace(ns: str, now) -> None:
    try:
        jobs = Nomad.jobs.get_jobs(namespace=ns, filter_='Status != "dead"')
    except Exception:
        LOG.exception("could not list jobs for namespace %s", ns)
        return

    seen = set()
    for stub in jobs:
        if not stub["Name"].startswith(config.JOB_PREFIXES):
            continue
        seen.add(stub["ID"])
        # the job list stub does not carry Meta, fetch the full job for `owner`
        try:
            j = Nomad.job.get_job(id_=stub["ID"], namespace=ns)
        except Exception:
            LOG.exception("could not fetch job %s", stub["ID"])
            continue
        try:
            process_single(info_from_job(j, ns), now)
        except mimir.MimirError as e:
            LOG.warning("Mimir unavailable, stopping the sweep of %s: %s", ns, e)
            return
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
        except mimir.MimirError as e:
            LOG.warning("Mimir unavailable, stopping the sweep of %s: %s", ns, e)
            return
        except Exception:
            LOG.exception("energy close failed for deployment %s", uuid)


# ---------------------------------------------------------------------------
# per-deployment sweep
# ---------------------------------------------------------------------------


def _deployment_buckets(
    doc: dict, allocs_by_dc: dict, alloc_windows: list, settled_ts: float, t: Clock
) -> list:
    """
    Energy buckets of [settled_ts, extract_to] summed over the deployment's
    datacenters, clipped to its actual life. Also records on `doc` the
    energy-weighted mean TUE of what was integrated.
    """
    if settled_ts >= t.extract_to:
        return []

    dq = config.get().get("data_quality")
    per_dc = []
    tue_norm = tue_raw = 0.0
    for dc, alloc_ids in allocs_by_dc.items():
        cpu, gpu = query.fetch_power(
            *compute.power_promql(alloc_ids), settled_ts, t.extract_to, t.step_s
        )
        if not cpu and not gpu:
            continue
        tue = config.tue(dc)
        buckets = compute.integrate(
            cpu, gpu, t.step_s, t.bucket_s, tue, data_quality=dq
        )
        e = sum(b.energy_wh for b in buckets)
        tue_norm += e
        tue_raw += e / tue
        per_dc.append(_with_footprint(dc, buckets, settled_ts, t))

    if tue_raw:
        doc["tue_factor"] = tue_norm / tue_raw
    elif "tue_factor" not in doc and allocs_by_dc:
        doc["tue_factor"] = config.tue(next(iter(allocs_by_dc)))

    new_buckets = _complete_buckets(compute.merge_buckets(per_dc), t)
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
                compute.parse_iso(b.ts),
                compute.parse_iso(b.ts) + t.bucket_s,
            )
            > 0
        ]
    return new_buckets


def process_single(info: dict, now, closing: bool = False) -> None:
    t = _clock(now)
    ns, uuid = info["namespace"], info["uuid"]

    doc = store.read_accum(ns, uuid) or {}
    doc.pop("partial", None)  # dropped in favour of `coverage_ratio` (always a float)

    settled_ts, complete = _resume_point(doc, t, info.get("submit_time"))
    window_start = _window_start(ns, uuid, doc, settled_ts, t)

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
            "updated_at": compute.iso(t.now_ts),
            "active_allocs": running_allocs,
        }
    )
    if info.get("submit_time"):
        doc["submit_time"] = compute.iso(info["submit_time"])
        doc["submit_time_epoch"] = info["submit_time"]
    if allocs_by_dc:
        doc["datacenters"] = list(allocs_by_dc)
    if closing and not doc.get("closed_at"):
        doc["closed_at"] = compute.iso(t.now_ts)

    run_s = compute.running_seconds(alloc_windows, settled_ts, t.extract_to)

    # no monitored allocations: queued, or ran only in unmonitored sites
    if not allocs_by_dc:
        if "settled" not in doc and partial_dc and run_s > 2 * t.bucket_s:
            doc.update({"metrics_available": False, "coverage_ratio": 0.0})
        store.write_accum(ns, uuid, doc)
        return

    new_buckets = _deployment_buckets(doc, allocs_by_dc, alloc_windows, settled_ts, t)

    # best-effort coverage: no data while the deployment was clearly running
    # means the node has no monitoring stack yet -> null, retry next sweep.
    if not new_buckets and "settled" not in doc:
        if run_s > 2 * t.bucket_s:
            doc.update({"metrics_available": False, "coverage_ratio": 0.0})
            store.write_accum(ns, uuid, doc)
        return

    doc["metrics_available"] = True
    doc["window_start"] = compute.iso(window_start)

    aged_n, tail_n = _fold_and_accumulate(ns, uuid, doc, new_buckets, settled_ts, t)

    # coverage: fraction of the run time (including time on unmonitored sites, so
    # `partial_dc` is captured implicitly) that ended up with power data.
    covered_s = (tail_n + aged_n) * t.bucket_s
    doc["coverage_ratio"] = round(min(1.0, covered_s / run_s), 3) if run_s > 0 else 1.0

    store.write_accum(ns, uuid, doc)


def _fold_and_accumulate(
    ns: str,
    uuid: str,
    doc: dict,
    new_buckets: list,
    settled_ts: float,
    t: Clock,
) -> tuple[int, int]:
    """
    Merge `new_buckets` (Bucket namedtuples) over `doc["tail"]` keyed by `ts`,
    age the buckets older than `revision_cut` into `doc["settled"]` (appending
    them to the persisted series), and recompute `settled_ts` / `accumulated` /
    `pointer_ts` on `doc`. Returns `(aged_count, tail_count)`.

    Shared by the per-deployment (`process_single`) and per-datacenter
    (`process_datacenter`) sweeps.
    """
    settled = dict(doc.get("settled") or config.EMPTY)

    by_ts = {b["ts"]: b for b in (doc.get("tail") or [])}
    for b in new_buckets:
        by_ts[b.ts] = b._asdict()

    tail, aged_out = [], []
    for ts in sorted(by_ts):
        (aged_out if compute.parse_iso(ts) < t.revision_cut else tail).append(by_ts[ts])

    if aged_out:
        settled = config.add_totals(settled, config.sum_totals(aged_out))
        store.append_series(ns, uuid, aged_out)
        settled_ts = max(compute.parse_iso(b["ts"]) for b in aged_out) + t.bucket_s

    doc["settled"] = settled
    doc["tail"] = tail
    doc["settled_ts"] = compute.iso(settled_ts)
    doc["accumulated"] = config.add_totals(settled, config.sum_totals(tail))
    doc["pointer_ts"] = compute.iso(
        (compute.parse_iso(tail[-1]["ts"]) + t.bucket_s) if tail else settled_ts
    )
    return len(aged_out), len(tail)


# ---------------------------------------------------------------------------
# per-datacenter sweep (cluster-level energy accumulator)
# ---------------------------------------------------------------------------


def _sweep_cluster(now) -> None:
    for dc in config.get()["metrics_datacenters"]:
        try:
            process_datacenter(dc, now)
        except Exception:
            LOG.exception("cluster energy sweep failed for datacenter %s", dc)


def _cluster_buckets(dc: str, settled_ts: float, t: Clock) -> list:
    if settled_ts >= t.extract_to:
        return []
    cpu, gpu = query.fetch_power(
        *compute.cluster_promql(dc), settled_ts, t.extract_to, t.step_s
    )
    buckets = compute.integrate(
        cpu, gpu, t.step_s, t.bucket_s, config.tue(dc), data_quality=config.cluster_dq()
    )
    return _complete_buckets(_with_footprint(dc, buckets, settled_ts, t), t)


def process_datacenter(dc: str, now) -> None:
    t = _clock(now)
    ns = config.CLUSTER_NS

    doc = store.read_accum(ns, dc) or {}
    settled_ts, complete = _resume_point(doc, t)
    window_start = _window_start(ns, dc, doc, settled_ts, t)

    doc.update(
        {
            "datacenter": dc,
            "deployment_uuid": dc,  # reuse the same key name as deployment docs
            "namespace": ns,
            "complete": complete,
            "updated_at": compute.iso(t.now_ts),
            "tue_factor": config.tue(dc),
        }
    )

    new_buckets = _cluster_buckets(dc, settled_ts, t)

    if not new_buckets and "settled" not in doc:
        # datacenter has no host-power series yet: retry next sweep, no zeros
        doc["metrics_available"] = False
        store.write_accum(ns, dc, doc)
        return

    doc["metrics_available"] = True
    doc["window_start"] = compute.iso(window_start)
    _fold_and_accumulate(ns, dc, doc, new_buckets, settled_ts, t)
    store.write_accum(ns, dc, doc)
