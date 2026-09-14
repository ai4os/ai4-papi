"""
Pure(ish) helpers for the energy-accounting feature: resolve a deployment's
allocations, build the PromQL, integrate power into energy buckets, apply the
footprint, and downsample.

The only side effect here is reading from Nomad in `resolve_alloc_ids` (and its
cached node->datacenter lookup); everything else is pure and unit-testable.
"""

import bisect
from collections import namedtuple
import datetime

from cachetools import cached, TTLCache

import ai4papi.conf as papiconf
from ai4papi.nomad_utils import Nomad
from ai4papi.wattnet import GreenDirector


# Bucket start `ts` is an ISO-8601 UTC string ("...Z"). Energy in Wh (normalized
# by the datacenter TUE), power in W, footprint per bucket.
Bucket = namedtuple("Bucket", ["ts", "energy_wh", "power_w_avg", "carbon_g", "water_l"])

DEFAULT_CARBON = GreenDirector.DEFAULTS["carbon"]  # gCO2eq/kWh
DEFAULT_WATER = GreenDirector.DEFAULTS["water"]  # L/kWh

_TERMINAL_STATUSES = {"complete", "failed", "lost"}


def _iso(ts: float) -> str:
    return (
        datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_iso(s: str) -> float:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------
# Nomad: deployment -> allocations
# ---------------------------------------------------------------------------

AllocWindow = namedtuple("AllocWindow", ["alloc_id", "start_ts", "end_ts"])


@cached(cache=TTLCache(maxsize=4096, ttl=60 * 60))
def _node_datacenter(node_id: str) -> str:
    return Nomad.node.get_node(node_id)["Datacenter"]


def resolve_alloc_ids(job_id: str, namespace: str):
    """
    Return `(allocs_by_dc, alloc_windows, running_allocs, partial_dc)` for every
    allocation the job ever had, keeping only datacenters that have power metrics
    (`energy.metrics_datacenters`).

    * allocs_by_dc: {datacenter: [alloc_id, ...]}  (whole lifetime)
    * alloc_windows: [AllocWindow(alloc_id, start_ts, end_ts|None)]  (all allocs,
      to estimate how long the deployment actually ran)
    * running_allocs: [{"alloc_id": ..., "datacenter": ...}]  (currently running,
      for the live top-up)
    * partial_dc: True if some allocation ran in a datacenter without metrics
    """
    metrics_dcs = set(papiconf.MAIN_CONF["energy"]["metrics_datacenters"])

    allocs = Nomad.job.get_allocations(id_=job_id, namespace=namespace)

    allocs_by_dc: dict[str, list[str]] = {}
    alloc_windows: list[AllocWindow] = []
    running_allocs: list[dict] = []
    partial_dc = False

    for a in allocs:
        try:
            dc = _node_datacenter(a["NodeID"])
        except Exception:
            continue

        start_ts = a["CreateTime"] / 1e9 if a.get("CreateTime") else None
        end_ts = None
        if a.get("ClientStatus") in _TERMINAL_STATUSES and a.get("ModifyTime"):
            end_ts = a["ModifyTime"] / 1e9
        if start_ts is not None:
            alloc_windows.append(AllocWindow(a["ID"], start_ts, end_ts))

        if dc in metrics_dcs:
            allocs_by_dc.setdefault(dc, []).append(a["ID"])
            if a.get("ClientStatus") == "running":
                running_allocs.append({"alloc_id": a["ID"], "datacenter": dc})
        else:
            partial_dc = True

    return allocs_by_dc, alloc_windows, running_allocs, partial_dc


def running_seconds(alloc_windows, start_ts: float, end_ts: float) -> float:
    """
    Union of the alloc windows clipped to [start_ts, end_ts]. Approximates how
    long the deployment was actually running in that interval (to compare
    against the time we have power data for).
    """
    spans = []
    for w in alloc_windows:
        s = max(w.start_ts, start_ts)
        e = min(w.end_ts if w.end_ts else end_ts, end_ts)
        if e > s:
            spans.append((s, e))
    if not spans:
        return 0.0
    spans.sort()
    total = 0.0
    cur_s, cur_e = spans[0]
    for s, e in spans[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
    total += cur_e - cur_s
    return total


# ---------------------------------------------------------------------------
# PromQL
# ---------------------------------------------------------------------------


def power_promql(alloc_ids: list[str]) -> tuple[str, str]:
    """
    (cpu_query, gpu_query) for a set of allocation ids (all in the same
    datacenter).

    Both are grouped, not fully summed, so the data-quality filter runs on the
    original per-source series (per allocation for CPU, per GPU for GPU power):
    a single bad reading is dropped without discarding the other sources at the
    same instant, and the ceiling / spike checks compare against a physically
    bounded signal. The integrator then sums the per-series energy.
    """
    regex = "|".join(alloc_ids)
    cpu_q = (
        "sum by (container_label_com_hashicorp_nomad_alloc_id) ("
        "scaph_process_power_consumption_microwatts"
        f'{{container_label_com_hashicorp_nomad_alloc_id=~"{regex}"}})'
    )
    gpu_q = (
        "DCGM_FI_DEV_POWER_USAGE"
        " * on(UUID, instance) group_left(alloc_id)"
        f' nomad_gpu_allocation_info{{alloc_id=~"{regex}"}}'
    )
    return cpu_q, gpu_q


# ---------------------------------------------------------------------------
# Integration: power samples -> energy buckets
# ---------------------------------------------------------------------------


def _series_samples(
    result: list[dict], scale: float
) -> list[list[tuple[float, float]]]:
    """A Mimir matrix result -> one sorted `[(ts, watts)]` list per series."""
    out: list[list[tuple[float, float]]] = []
    for series in result:
        s: list[tuple[float, float]] = []
        for ts, val in series.get("values", []):
            try:
                s.append((float(ts), float(val) * scale))
            except (TypeError, ValueError):
                continue
        if s:
            s.sort()
            out.append(s)
    return out


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def sanitize(
    samples: list[tuple[float, float]], dq: dict | None
) -> list[tuple[float, float]]:
    """
    Data-quality filter on one original power series (per allocation for CPU,
    per GPU for GPU power), before integration. Anomalous samples are *dropped*
    (the integrator then treats it as a gap), never clamped, so a bad reading
    never inflates the accumulated energy. Every check is skippable via
    `energy.data_quality`:

    * `min_power_w`  : drop samples below this (default 0, kills negatives)
    * `max_power_w`  : drop samples above this hard ceiling (0 = off). Physically
      bounded because it applies per source (one alloc / one GPU).
    * `spike_factor` : drop a sample above `spike_factor` * rolling median of its
      `spike_window` neighbours (0 = off)
    """
    if not dq or not dq.get("enabled", True):
        return samples

    lo = dq.get("min_power_w", 0.0)
    hi = dq.get("max_power_w", 0.0)
    out = [(t, p) for t, p in samples if p >= lo and (not hi or p <= hi)]

    factor = dq.get("spike_factor", 0.0)
    window = int(dq.get("spike_window", 11)) or 11
    if factor and len(out) > window:
        half = window // 2
        vals = [p for _, p in out]
        kept = []
        for i, (t, p) in enumerate(out):
            lo_i, hi_i = max(0, i - half), min(len(vals), i + half + 1)
            neighbours = vals[lo_i:i] + vals[i + 1 : hi_i]
            med = _median(neighbours)
            if med > 1e-6 and p > factor * med:
                continue  # spike -> drop
            kept.append((t, p))
        out = kept

    return out


def _integrate_series(samples, step_s, bucket_s, gap_factor):
    """One power series -> {bucket_ts: [energy_wh, covered_s]} (trapezoidal)."""
    max_gap = gap_factor * step_s
    out: dict[int, list[float]] = {}
    for (t0, p0), (t1, p1) in zip(samples, samples[1:]):
        dt = t1 - t0
        if dt <= 0 or dt > max_gap:
            continue
        wh = (p0 + p1) / 2 * dt / 3600
        b = int(t0 // bucket_s) * bucket_s
        cur = out.setdefault(b, [0.0, 0.0])
        cur[0] += wh
        cur[1] += dt
    return out


def integrate(
    cpu_result: list[dict],
    gpu_result: list[dict],
    step_s: int = 30,
    bucket_s: int = 900,
    tue_factor: float = 1.0,
    gap_factor: float = 3.0,
    data_quality: dict | None = None,
) -> list[Bucket]:
    """
    Per-source data-quality filter, then trapezoidal integral of every power
    series (CPU per allocation, GPU per GPU) into fixed `bucket_s` energy
    buckets, summed. Gaps longer than `gap_factor * step_s` are not integrated
    across (deployment stopped / scrape hole / dropped anomaly). `carbon_g` /
    `water_l` are left at 0 (see `apply_footprint`).
    """
    acc: dict[int, list[float]] = {}  # bucket_ts -> [raw_energy_wh, covered_s]

    series_lists = _series_samples(cpu_result, 1e-6) + _series_samples(gpu_result, 1.0)
    for samples in series_lists:
        clean = sanitize(samples, data_quality)
        if len(clean) < 2:
            continue
        for b, (e, c) in _integrate_series(clean, step_s, bucket_s, gap_factor).items():
            cur = acc.setdefault(b, [0.0, 0.0])
            cur[0] += e
            cur[1] = max(cur[1], c)  # max, not sum: covered time of the busiest source

    buckets = []
    for b in sorted(acc):
        raw_wh, covered = acc[b]
        energy_wh = raw_wh * tue_factor
        hours = covered / 3600 if covered else 0.0
        buckets.append(
            Bucket(
                ts=_iso(b),
                energy_wh=energy_wh,
                # mean power over the covered part of the bucket, normalized like
                # energy_wh so `energy_wh ~= power_w_avg * bucket_hours`
                power_w_avg=(energy_wh / hours) if hours else 0.0,
                carbon_g=0.0,
                water_l=0.0,
            )
        )
    return buckets


def instant_power(
    cpu_result: list[dict],
    gpu_result: list[dict],
    tue_factor: float,
    fresh_after_ts: float,
    data_quality: dict | None = None,
) -> float | None:
    """
    Current draw from a range query: the sum of the most recent (sanitized)
    sample of every power series, normalized by TUE. A series whose last good
    sample predates `fresh_after_ts` (source gone, scrape hole) is skipped.
    `None` when nothing is fresh.
    """
    raw_w, fresh = 0.0, False
    for scale, result in ((1e-6, cpu_result), (1.0, gpu_result)):
        for samples in _series_samples(result, scale):
            clean = sanitize(samples, data_quality)
            if clean and clean[-1][0] >= fresh_after_ts:
                raw_w += clean[-1][1]
                fresh = True
    return raw_w * tue_factor if fresh else None


def merge_buckets(bucket_lists: list[list[Bucket]]) -> list[Bucket]:
    """
    Sum buckets from several sources by timestamp: the datacenters a deployment
    ran in, or the deployments of one user. Inputs share the 15-min grid.
    """
    acc: dict[str, Bucket] = {}
    for buckets in bucket_lists:
        for bk in buckets:
            cur = acc.get(bk.ts)
            if cur is None:
                acc[bk.ts] = bk
            else:
                acc[bk.ts] = cur._replace(
                    energy_wh=cur.energy_wh + bk.energy_wh,
                    power_w_avg=cur.power_w_avg + bk.power_w_avg,
                    carbon_g=cur.carbon_g + bk.carbon_g,
                    water_l=cur.water_l + bk.water_l,
                )
    return [acc[k] for k in sorted(acc)]


# ---------------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------------


def _prep_series(series):
    """[[iso_ts, value], ...] -> (sorted ts floats, values) for bisect lookup."""
    if not series:
        return [], []
    parsed = sorted((_parse_iso(t), float(v)) for t, v in series)
    return [p[0] for p in parsed], [p[1] for p in parsed]


def intensity_at(ts_arr, val_arr, ts: float, default: float) -> float:
    """Value of the last sample with sample_ts <= ts; `default` if none."""
    if not ts_arr:
        return default
    i = bisect.bisect_right(ts_arr, ts)
    return val_arr[i - 1] if i > 0 else default


def apply_footprint(
    buckets: list[Bucket],
    carbon_series=None,
    water_series=None,
) -> list[Bucket]:
    """
    Fill `carbon_g` / `water_l` per bucket = (energy_wh / 1000) * intensity(ts),
    with the Wattnet 15-min series (already normalized energy). Falls back to
    GreenDirector.DEFAULTS when a series is missing or predates the bucket.
    """
    c_ts, c_val = _prep_series(carbon_series)
    w_ts, w_val = _prep_series(water_series)

    out = []
    for bk in buckets:
        ts = _parse_iso(bk.ts)
        kwh = bk.energy_wh / 1000
        out.append(
            bk._replace(
                carbon_g=kwh * intensity_at(c_ts, c_val, ts, DEFAULT_CARBON),
                water_l=kwh * intensity_at(w_ts, w_val, ts, DEFAULT_WATER),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Downsampling (for the time-series responses)
# ---------------------------------------------------------------------------


def downsample(buckets: list[Bucket], target_points: int) -> list[Bucket]:
    """Aggregate consecutive buckets so the result has at most `target_points`."""
    n = len(buckets)
    if n <= target_points or target_points < 1:
        return buckets

    group = -(-n // target_points)  # ceil
    out = []
    for i in range(0, n, group):
        chunk = buckets[i : i + group]
        out.append(
            Bucket(
                ts=chunk[0].ts,
                energy_wh=sum(b.energy_wh for b in chunk),
                power_w_avg=sum(b.power_w_avg for b in chunk) / len(chunk),
                carbon_g=sum(b.carbon_g for b in chunk),
                water_l=sum(b.water_l for b in chunk),
            )
        )
    return out
