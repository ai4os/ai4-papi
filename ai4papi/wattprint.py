"""
Utilities for the integration with WattPrint (https://wattprint.eu/)
"""

import datetime
import json
import requests
import os

from cachetools import cached, TTLCache


WATTPRINT_EMAIL = "bot@ai4eosc.eu"
WATTPRINT_PASS = os.environ.get("WATTPRINT_PASSWORD")
if not WATTPRINT_PASS:
    print("You should define a WATTPRINT_PASSWORD")


@cached(cache=TTLCache(maxsize=1024, ttl=20 * 60 * 60))
def retrieve_token():
    """
    WattPrint tokens last only one day, so we cache the response for 20 hours.
    """

    url = "https://api.wattprint.eu/token-request/get_token"
    headers = {"Content-Type": "application/json"}
    data = {"email": WATTPRINT_EMAIL, "password": WATTPRINT_PASS}
    response = requests.post(url, headers=headers, data=json.dumps(data))
    return response.json()["access_token"]


@cached(cache=TTLCache(maxsize=1024, ttl=15 * 60))
def last_footprint(lon, lat):
    """
    Retrieve the last footprint for a given lon-lat location.
    WattPrint has a temporal resolution of 15 minutes, so we cache for that amount of
    time.
    """
    try:
        url = "https://api.wattprint.eu/v1/footprints"
        end = datetime.datetime.now()
        start = end - datetime.timedelta(hours=6)

        params = {
            "lat": lat,
            "lon": lon,
            "footprint_type": "carbon",
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "aggregate": "false",
        }
        token = retrieve_token()
        headers = {"Authorization": f"Bearer {token}"}

        response = requests.get(url, headers=headers, params=params)

        # For the time being, we take the last series value, irrespectively of whether
        # it belongs to the series [valid=True] or [valid=False]
        timestamps, footprints = [], []
        for series in response.json()[0]["series"]:
            for ts, fp in series["values"]:
                # Sometimes the lasts returned footprints are zero because they
                # are not yet computed. We ignore those.
                if fp != 0:
                    timestamps.append(ts)
                    footprints.append(round(fp, 2))

        # Sort timestamps and footprints based on timestamps
        timestamps, footprints = zip(*sorted(zip(timestamps, footprints)))

        return footprints[-1]

    except Exception as e:
        # We return a default value
        print(f"Failed to retrieve footprint: {e}")
        return 301


def datacenter_affinities(datacenters):
    """
    Map linearly a carbon footprint into a datacenter Nomad affinity.
    It's an inverse relation:
    * Maximum carbon footprint should have minimum affinity (e.g. 0)
    * Minimum carbon footprint should have maximum affinity (e.g. 20)

    We keep the maximum affinity well below the theoretical maximum (100) to not
    interfere too much with other constraints/affinities
    """
    # Affinity range
    af_min, af_max = 0, 30
    # Footprint range
    footprints = [i["energy_quality"] for i in datacenters.values()]
    fp_min, fp_max = min(footprints), max(footprints)

    # Inverse linear mapping
    affinities = {}
    for name, dc in datacenters.items():
        if fp_min != fp_max:
            x = dc["energy_quality"]
            affinities[name] = af_max + (af_min - af_max) * (x - fp_min) / (
                fp_max - fp_min
            )
        else:  # avoid dividing by zero
            affinities[name] = 0
    return affinities
