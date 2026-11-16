"""
On-demand sweep for the read path of the energy-accounting feature.

When a deployment has no accumulator doc yet (just deployed, or the background
sweep has not reached it), the read API sweeps it on the spot instead of
waiting up to `sweep_seconds`: `ensure_swept` for a single deployment,
`ensure_swept_bulk` for the list / per-user reads. `live_only_doc` covers the
last gap, a deployment too young for even one completed bucket.
"""

import logging
import threading

from cachetools import cached

from ai4papi.clients import mimir
from ai4papi.energy import compute, config, store, sweep
from ai4papi.nomad_utils import Nomad

LOG = logging.getLogger(__name__)


def _sweep_one(ns: str, uuid: str, now) -> bool:
    """Fetch one Nomad job and run `process_single` on it. `False` when the job
    cannot be found (not a deployment, or already gone) or the sweep failed."""
    try:
        j = Nomad.job.get_job(id_=uuid, namespace=ns)
    except Exception:
        return False
    try:
        sweep.process_single(sweep.info_from_job(j, ns), now)
    except mimir.MimirError as e:
        LOG.warning("on-demand energy sweep skipped, Mimir unavailable: %s", e)
        return False
    except Exception:
        LOG.warning(
            "on-demand energy sweep failed for %s/%s",
            config.log_safe(ns),
            config.log_safe(uuid),
            exc_info=True,
        )
        return False
    return True


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
    if not config.enabled():
        return None
    doc = store.read_accum(ns, uuid)
    if doc is not None:
        return doc
    if not _sweep_one(ns, uuid, config.now()):
        return None
    return store.read_accum(ns, uuid)


_bulk_sweep_cache = config.topup_cache(2048)


@cached(_bulk_sweep_cache, lock=threading.Lock())
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
    if not config.enabled():
        return []
    try:
        jobs = Nomad.jobs.get_jobs(
            namespace=ns,
            filter_=f'Meta.owner == "{owner}" and Status != "dead"',
        )
    except Exception:
        LOG.warning(
            "could not list jobs for on-demand sweep (%s/%s)",
            config.log_safe(ns),
            config.log_safe(owner),
        )
        return []
    now = config.now()
    stubs = [s for s in jobs if s["Name"].startswith(config.JOB_PREFIXES)]
    for stub in stubs:
        if store.read_accum(ns, stub["ID"]) is not None:
            continue
        _sweep_one(ns, stub["ID"], now)
    return stubs


_live_only_doc_cache = config.topup_cache(8192)


@cached(_live_only_doc_cache, lock=threading.Lock())
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
    `energy._to_stats` / `_apply_topup` / `_build_series` -- treats it like
    a real doc with an all-zero accumulator and `pointer_ts = submit_time`: the
    whole reported number then comes from the live top-up's Mimir query, capped
    at the same lookback `topup` itself uses. `None` if the job cannot be
    resolved to a running, monitored allocation (not found, queued, or no
    allocation yet).
    """
    if not config.enabled():
        return None
    try:
        j = Nomad.job.get_job(id_=uuid, namespace=ns)
    except Exception:
        return None
    info = sweep.info_from_job(j, ns)
    try:
        allocs_by_dc, _windows, running_allocs, _partial = compute.resolve_alloc_ids(
            uuid, ns
        )
    except Exception:
        return None
    if not running_allocs:
        return None
    submit = info.get("submit_time")
    submit_ts = submit or config.now().timestamp()
    # `window_start` below is pinned to the exact submit time, same as
    # `process_single`'s first-encounter `window_start` -- not started late or
    # clamped, so `complete` is `True` unless we had no real `submit_time` to
    # pin it to at all (job fetched with no `SubmitTime`, an edge case) or it
    # somehow predates the lookback window (impossible in practice: this path
    # only ever runs for a deployment too young to have a doc yet).
    complete = (
        bool(submit)
        and submit
        >= config.now().timestamp() - config.get()["initial_lookback_hours"] * 3600
    )
    return {
        "deployment_uuid": uuid,
        "namespace": ns,
        "job_name": info.get("job_name"),
        "owner": info.get("owner"),
        "status": "running",
        "metrics_available": True,
        "complete": complete,
        "window_start": compute.iso(submit_ts),
        "settled_ts": compute.iso(submit_ts),
        "pointer_ts": compute.iso(submit_ts),
        "active_allocs": running_allocs,
        "datacenters": sorted(allocs_by_dc),
        "tail": [],
        "settled": dict(config.EMPTY),
        "accumulated": dict(config.EMPTY),
    }
