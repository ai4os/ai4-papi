"""
Utilities for the integration with WattNet.
API reference: https://api.wattnet.eu/v1/docs
"""

import datetime
import json
import requests
import os
import statistics

from cachetools import cached, TTLCache


session = requests.Session()

WATTPRINT_URL = "https://api.wattnet.eu"
WATTPRINT_EMAIL = "bot@ai4eosc.eu"
WATTPRINT_PASS = os.environ.get("WATTPRINT_PASSWORD")
if not WATTPRINT_PASS:
    print("You should define a WATTPRINT_PASSWORD")


class GreenDirector:
    # Define sensible default footprint values for datacenter outside WattNet scope (Europe)
    DEFAULT_ENERGY_QUALITY = 301  # in gCO2/kWh
    DEFAULT_WATER_USAGE = 12  # in L/kWh

    def __init__(self, datacenters: dict[str, dict], algorithm: str = "linear_rank"):
        """
        Green metrics are saved in the metrics var.

        Parameters
        ----------
        datacenters : dict of dicts
            Dictionary where keys are datacenter names and values are dicts
            containing at least 'lat', 'lon', and 'PUE' keys.
        algorithm : str
            Name of the ranking algorithm to use.
        """
        # Validate datacenters
        if not isinstance(datacenters, dict) or not datacenters:
            raise ValueError("'datacenters' must be a non-empty dictionary.")

        required_keys = {"lat", "lon", "PUE"}
        for name, dc in datacenters.items():
            if not isinstance(dc, dict):
                raise ValueError(
                    f"Datacenter '{name}' must be a dictionary, got {type(dc).__name__}."
                )
            missing = required_keys - dc.keys()
            if missing:
                raise ValueError(
                    f"Datacenter '{name}' is missing required keys: {missing}."
                )

        # Validate algorithm (collect available algorithms first)
        available_algorithms = set()
        for name in dir(self):
            method = getattr(self, name)
            if callable(method) and getattr(method, "_is_algorithm", False):
                available_algorithms.add(name.lstrip("_"))

        if algorithm not in available_algorithms:
            raise ValueError(
                f"Unknown algorithm '{algorithm}'. "
                f"Available algorithms: {sorted(available_algorithms)}."
            )

        # Init vars
        self.algorithm = algorithm
        self.datacenters = datacenters
        self.metrics = {k: {"carbon": [], "water": []} for k in datacenters.keys()}

    @cached(cache=TTLCache(maxsize=1024, ttl=20 * 60 * 60))
    def _retrieve_token(self):
        """
        WattPrint tokens last only one day, so we cache the response for 20 hours.
        """
        url = f"{WATTPRINT_URL}/token-request/get_token"
        headers = {"Content-Type": "application/json"}
        data = {"email": WATTPRINT_EMAIL, "password": WATTPRINT_PASS}
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        return response.json()["access_token"]

    @cached(cache=TTLCache(maxsize=1024, ttl=15 * 60))
    def _fetch_footprint_data(self, lat, lon):
        """
        Fetch footprint data from WattNet for a specific location.
        Cached outside the class because other.
        """
        url = f"{WATTPRINT_URL}/v1/footprints"
        end = datetime.datetime.now()
        start = end - datetime.timedelta(days=7)
        params = {
            "lat": lat,
            "lon": lon,
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "aggregate": "false",
        }
        token = self._retrieve_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = session.get(url, headers=headers, params=params)
        if not response.ok:
            print(
                f"[wattnet] Failed to retrieve footprint for coordinates ({lat}, {lon})"
            )
            return []
        return response.json()

    def retrieve_footprints(self):
        """
        Retrieve the last footprint for a given lon-lat location.
        WattPrint has a temporal resolution of 15 minutes, so we cache for that
        amount of time.
        If we are unable to retrieve a value (e.g. because location is outside Europe),
        we return a reasonable default value.
        """
        end = datetime.datetime.now()
        start = end - datetime.timedelta(days=7)

        for k, v in self.datacenters.items():
            data = self._fetch_footprint_data(v["lat"], v["lon"])

            # For each footprint type, we concatenate all timeseries, irrespectively of
            # whether they are [valid=True] (meaning their values are final) or
            # [valid=False] (meaning that they are an estimation, subject to change)
            for footprint in data:
                fp_type = footprint["footprint_type"]
                series = []
                for sublist in footprint["series"]:
                    series += sublist["values"]
                self.metrics[k][fp_type] = series

            # For datacenters outside Europe (e.g. Tubitak), WattNet offers no data
            # Therefore we return timeseries with default values
            if not data:
                round_end = end.replace(
                    minute=end.minute - (end.minute % 15), second=0, microsecond=0
                )
                round_start = start.replace(
                    minute=start.minute - (start.minute % 15), second=0, microsecond=0
                )
                carbon_series = []
                water_series = []
                current = round_start
                while current <= round_end:
                    ts = current.strftime("%Y-%m-%dT%H:%M:%SZ")
                    carbon_series.append([ts, self.DEFAULT_ENERGY_QUALITY])
                    water_series.append([ts, self.DEFAULT_WATER_USAGE])
                    current += datetime.timedelta(minutes=15)

                self.metrics[k]["carbon"] = carbon_series
                self.metrics[k]["water"] = water_series

    @staticmethod
    def algorithm(func):
        """Decorator to mark a method as a ranking algorithm."""
        func._is_algorithm = True
        return func

    @algorithm.__func__
    def _linear_rank(self, datacenters):
        """
        We map linearly a carbon footprint into a datacenter Nomad affinity.
        It's an inverse relation:
        * Maximum carbon footprint should have minimum affinity (0)
        * Minimum carbon footprint should have maximum affinity (100)

        Carbon footprint is computed as (energy quality * PUE).
        """
        # Affinity range
        af_min, af_max = 0, 100

        # Footprint range
        footprints = {}
        for k, v in datacenters.items():
            mean_quality = statistics.mean([i[1] for i in self.metrics[k]["carbon"]])
            footprints[k] = mean_quality * v["PUE"]
        fp_min, fp_max = min(footprints.values()), max(footprints.values())

        # Inverse linear mapping
        affinities = {}
        for k, x in footprints.items():
            if fp_min != fp_max:
                affinities[k] = round(
                    af_max + (af_min - af_max) * (x - fp_min) / (fp_max - fp_min)
                )
            else:  # avoid dividing by zero
                affinities[k] = 0

        return affinities

    def rank(self, subset: list = None):
        """
        Compute affinities for datacenter.
        We allow to specify a subset of datacenters, to match the fact that each user
        only sees the datacenters belonging to their VO.
        """
        if subset is None or not subset:
            subset = self.datacenters.keys()

        datacenters = {k: v for k, v in self.datacenters.items() if k in subset}
        algorithm = getattr(self, f"_{self.algorithm}")
        return algorithm(datacenters)
