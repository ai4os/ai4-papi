"""
Time-series building of the energy-accounting feature: turns persisted buckets
and live Mimir tails into the points served by the API.
"""

from ai4papi.energy import compute


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


def persisted(archive: list[dict], doc: dict) -> list[dict]:
    """Persisted 15-min buckets of one entity: the consolidated archive plus the
    still-mutable 24h tail."""
    return list(archive) + list(doc.get("tail") or [])


def _fill_gaps(
    buckets: list[compute.Bucket], step_s: int, end_ts: float
) -> list[compute.Bucket]:
    """Zero buckets on the `step_s` grid wherever `buckets` (sorted) has a hole,
    and from the last one up to `end_ts` (inclusive)."""
    by_ts = {b.ts: b for b in buckets}
    out, t = [], compute.parse_iso(buckets[0].ts)
    end = max(end_ts, compute.parse_iso(buckets[-1].ts))
    while t <= end:
        ts = compute.iso(t)
        out.append(by_ts.get(ts) or compute.Bucket(ts, 0.0, 0.0, 0.0, 0.0))
        t += step_s
    return out


def build_series(
    persisted_lists: list[list[dict]],
    live: list[list[dict]],
    target_points: int,
    fill_s: int | None = None,
    until: float | None = None,
) -> list[dict]:
    """
    Time series of one or several entities (a deployment, a user's deployments,
    a datacenter). The persisted buckets are merged by timestamp across entities
    and `compute.downsample`d once together with the live Mimir tails (merged the
    same way, only the part after the persisted buckets), so the resolution is
    uniform and capped at `target_points`. With `fill_s`, holes in the persisted buckets (no entity
    active) are filled with zero points on that grid, before downsampling, so
    the series is continuous; with `until` (a timestamp) it also runs up to the last
    complete bucket before it, stopping short of the live tail's first bucket. The points carry the per-bucket increments only; a running
    total is `cumsum(series[].energy_wh)` and matches `accumulated.energy_wh`.
    """

    def merged(lists: list[list[dict]]) -> list[compute.Bucket]:
        out = compute.merge_buckets([[_bucket(b) for b in bs] for bs in lists])
        return sorted(out, key=lambda b: b.ts)

    buckets = merged(persisted_lists)
    live_buckets = merged(live)
    if fill_s and buckets:
        end = (int(until) // fill_s - 1) * fill_s if until else 0.0
        if live_buckets:  # leave the live buckets to the live tail
            end = min(end, compute.parse_iso(live_buckets[0].ts) - fill_s)
        buckets = _fill_gaps(buckets, fill_s, end)
    # the live tail goes through the same downsampling, so every point spans the
    # same time; a point is `live` if its window holds any live bucket
    last = buckets[-1].ts if buckets else ""
    tail = [b for b in live_buckets if b.ts > last]
    points = compute.downsample(buckets + tail, target_points)
    if not tail:
        return [_point(p, live=False) for p in points]
    live_from = max(p.ts for p in points if p.ts <= tail[0].ts)
    return [_point(p, live=p.ts >= live_from) for p in points]
