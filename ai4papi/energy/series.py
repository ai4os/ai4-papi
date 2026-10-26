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


def build_series(
    persisted_lists: list[list[dict]], live: list[list[dict]], target_points: int
) -> list[dict]:
    """
    Time series of one or several entities (a deployment, a user's deployments,
    a datacenter). The persisted buckets are merged by timestamp across entities
    and `compute.downsample`d once, so the resolution is uniform and capped at
    `target_points`; the live Mimir tails are merged the same way and appended
    un-downsampled. The points carry the per-bucket increments only; a running
    total is `cumsum(series[].energy_wh)` and matches `accumulated.energy_wh`.
    """

    def merged(lists: list[list[dict]]) -> list[compute.Bucket]:
        out = compute.merge_buckets([[_bucket(b) for b in bs] for bs in lists])
        return sorted(out, key=lambda b: b.ts)

    series = [
        _point(b, live=False)
        for b in compute.downsample(merged(persisted_lists), target_points)
    ]
    series += [
        _point(b, live=True)
        for b in merged(live)
        if not series or b.ts > series[-1]["ts"]
    ]
    return series
