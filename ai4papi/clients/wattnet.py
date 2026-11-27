"""
Client for the Wattnet API, built on the official `wattnet-client` library.

Resolves AI4EOSC datacenters to their coordinates and returns the carbon/water
footprint (and environmental score) series in the `[[iso_ts, value], ...]`
format used across PAPI.
API reference: https://api.wattnet.eu/v1/docs
"""

import datetime
import os

from cachetools import TTLCache, cached
from wattnet.client import ConfigurationError, WattnetRawClient

import ai4papi.conf as papiconf

TIMEOUT = 30  # seconds; without it a stalled Wattnet would block the background threads

WATTNET_CLIENT_ID = os.environ.get("WATTNET_CLIENT_ID")
WATTNET_CLIENT_SECRET = os.environ.get("WATTNET_CLIENT_SECRET")
if not (WATTNET_CLIENT_ID and WATTNET_CLIENT_SECRET):
    print("You should define WATTNET_CLIENT_ID / WATTNET_CLIENT_SECRET")

# The library fetches, caches and renews the OAuth2 token on its own
try:
    client = WattnetRawClient(
        client_id=WATTNET_CLIENT_ID,
        client_secret=WATTNET_CLIENT_SECRET,
        timeout=TIMEOUT,
        # no retries: GreenDirector's backoff already covers them, and retries on a
        # stalled Wattnet would make one refresh round outlast that backoff
        retry_count=0,
    )
except ConfigurationError:
    client = None  # calls raise, callers fall back to GreenDirector.DEFAULTS


def _flatten(payload: list, fp_type: str = None) -> dict:
    """
    Join every `series[].points` block (valid or not, final or estimated) of each
    footprint type into one `[[iso_ts, value], ...]` list sorted by timestamp.
    """
    out = {}
    for item in payload:
        series = [
            [p["timestamp"], p["value"]]
            for block in item.get("series", [])
            for p in block.get("points", [])
        ]
        out[fp_type or item["footprint_type"]] = sorted(series, key=lambda x: x[0])
    return out


def metrics(
    datacenter: str,
    start: datetime.datetime,
    end: datetime.datetime,
    score: bool = False,
) -> dict:
    """
    Carbon/water intensity series of a datacenter over [start, end], plus the
    environmental score if `score`. Returns `{}` for a datacenter without
    coordinates. Wattnet errors are raised, the caller decides the fallback.
    """
    dc = papiconf.datacenters.get(datacenter, {})
    lat, lon = dc.get("lat"), dc.get("lon")
    if lat is None or lon is None:
        return {}
    if client is None:
        raise RuntimeError("Wattnet credentials are not configured")

    params = {"lat": lat, "lon": lon, "start": start, "end": end}
    out = _flatten(client.get_footprints(**params))
    if score:
        out.update(
            _flatten(
                client.get_environmental_score(**params),
                fp_type="environmental-score",
            )
        )
    return out


@cached(cache=TTLCache(maxsize=1024, ttl=15 * 60))
def latest_metrics(datacenter: str) -> dict:
    """
    Last 7 days of footprints and environmental score of a datacenter. Wattnet has
    a temporal resolution of 15 minutes, so we cache for that amount of time.
    Raises instead of returning empty data so a failure is not cached
    (cachetools only caches return values).
    """
    end = datetime.datetime.now(datetime.UTC)
    data = metrics(datacenter, end - datetime.timedelta(days=7), end, score=True)
    if not all(data.get(k) for k in ("carbon", "water", "environmental-score")):
        raise RuntimeError(f"Empty Wattnet data for {datacenter}")
    return data
