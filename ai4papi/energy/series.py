"""
Time-series building of the energy-accounting feature: turns persisted buckets
and live Mimir tails into the points served by the API.
"""

from ai4papi.energy import compute

# Windows the 15-min buckets are aggregated into, finest first. Every one is a
# multiple of 15 min, and from 1 h on of the hour, and they tile the UTC day (or
# are whole weeks), so the buckets always line up and a client grouping the points
# by day / ISO week gets exact totals.
RESOLUTIONS: dict[str, int] = {
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "3h": 10800,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


def pick_resolution(first_ts: float, last_ts: float, max_points: int) -> str:
    """
    The finest window whose number of points over `[first_ts, last_ts]` stays
    within `max_points`. The cap is soft: the windows are a fixed ladder, so a
    series usually ends up under it, by up to a factor of two between rungs. Beyond
    the coarsest window (weeks) the cap no longer applies.
    """
    for name, w in RESOLUTIONS.items():
        o = compute.WINDOW_ANCHOR_S
        if int((last_ts - o) // w) - int((first_ts - o) // w) + 1 <= max_points:
            return name
    return "1w"


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
    """`buckets` (sorted) plus a zero bucket on the `step_s` grid at every missing
    slot, from the first bucket up to `end_ts` (inclusive). Buckets off the grid
    are kept, so no energy is lost."""
    have = {b.ts for b in buckets}
    out = list(buckets)
    t = compute.parse_iso(buckets[0].ts)
    end = max(end_ts, compute.parse_iso(buckets[-1].ts))
    while t <= end:
        ts = compute.iso(t)
        if ts not in have:
            out.append(compute.Bucket(ts, 0.0, 0.0, 0.0, 0.0))
        t += step_s
    return sorted(out, key=lambda b: b.ts)


def build_series(
    persisted_lists: list[list[dict]],
    live: list[list[dict]],
    max_points: int,
    fill_s: int | None = None,
    until: float | None = None,
) -> list[dict]:
    """
    Time series of one or several entities (a deployment, a user's deployments,
    a datacenter). The persisted buckets are merged by
    timestamp across entities and `compute.downsample`d together with the live
    Mimir tails (merged the same way, only the part after the persisted buckets)
    into fixed windows: the finest of `RESOLUTIONS` that keeps the series within
    about `max_points` (`pick_resolution`), so every point spans the same time. With `fill_s`, holes in the persisted
    buckets (no entity active) are filled with zero points on that grid, before
    aggregating, so the series is continuous; with `until` (a timestamp) it also
    runs up to the last complete bucket before it, stopping short of the live
    tail's first bucket. The points carry the per-window increments only; a
    running total is `cumsum(series[].energy_wh)` and matches
    `accumulated.energy_wh`.
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
    # the live tail goes through the same aggregation, so every point spans the
    # same time; a point is `live` if its window holds any live bucket
    last = buckets[-1].ts if buckets else ""
    tail = [b for b in live_buckets if b.ts > last]
    allb = buckets + tail
    if not allb:
        return []
    name = pick_resolution(
        compute.parse_iso(allb[0].ts), compute.parse_iso(allb[-1].ts), max_points
    )
    w = RESOLUTIONS[name]
    points = compute.downsample(allb, w)
    if not tail:
        return [_point(p, live=False) for p in points]
    live_from = max(p.ts for p in points if p.ts <= tail[0].ts)
    return [_point(p, live=p.ts >= live_from) for p in points]
