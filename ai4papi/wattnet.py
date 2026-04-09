"""
Utilities for the integration with WattNet.
API reference: https://api.wattnet.eu/v1/docs
"""

import datetime
import json
import requests
import os
import statistics
import warnings

from cachetools import cached, TTLCache


session = requests.Session()

WATTNET_URL = "https://api.wattnet.eu"
WATTNET_EMAIL = "bot@ai4eosc.eu"
WATTNET_PASS = os.environ.get("WATTNET_PASSWORD")
if not WATTNET_PASS:
    print("You should define a WATTNET_PASSWORD")


class GreenDirector:
    # Define sensible default footprint values for datacenter outside WattNet scope (Europe)
    DEFAULTS = {
        "carbon": 301,  # default energy quality in gCO2/kWh
        "water": 12,  # default water usage in L/kWh
        "green-score": 50,  # default green score (combining carbon and water).
    }

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
        WattNet tokens last only one day, so we cache the response for 20 hours.
        """
        url = f"{WATTNET_URL}/token-request/get_token"
        headers = {"Content-Type": "application/json"}
        data = {"email": WATTNET_EMAIL, "password": WATTNET_PASS}
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        return response.json()["access_token"]

    @cached(cache=TTLCache(maxsize=1024, ttl=15 * 60))
    def _fetch_footprint_data(self, lat, lon):
        """
        Fetch footprint data and green score data from WattNet for a specific
        lat-lon location. WattNet has a temporal resolution of 15 minutes, so we
        cache for that amount of time.
        """
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

        r1 = session.get(f"{WATTNET_URL}/v1/footprints", headers=headers, params=params)
        r2 = session.get(
            f"{WATTNET_URL}/v1/green-score", headers=headers, params=params
        )
        if not (r1.ok and r2.ok):
            warnings.warn(
                f"[wattnet] Failed to retrieve WattNet data for coordinates ({lat}, {lon})"
            )
            return []

        footprints = r1.json()
        score = r2.json()
        score[0].update({"footprint_type": "green-score"})

        return footprints + score

    def retrieve_footprints(self):
        """
        Retrieve the footprints for all datacenters.
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
                # Make sure the joined timeseries is sorted by timestamp
                series = sorted(series, key=lambda data: data[0])
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
                self.metrics[k] = {fp_type: [] for fp_type in self.DEFAULTS.keys()}
                current = round_start
                while current <= round_end:
                    ts = current.strftime("%Y-%m-%dT%H:%M:%SZ")
                    for fp_type, default_value in self.DEFAULTS.items():
                        self.metrics[k][fp_type].append([ts, default_value])
                    current += datetime.timedelta(minutes=15)

    @staticmethod
    def algorithm(func):
        """Decorator to mark a method as a ranking algorithm."""
        func._is_algorithm = True
        return func

    @algorithm.__func__
    def _linear_rank(self, datacenters, metric: str = "green-score"):
        """
        We map linearly a footprint (weighted with datacenter PUE) into a datacenter
        Nomad affinity.

        In the case of a carbon/water footprint, it's an inverse linear relation:
        * Maximum carbon footprint should have minimum affinity (0)
        * Minimum carbon footprint should have maximum affinity (100)

        In the case of a green score, it's a linear relation:
        * Minimum green score should have minimum affinity (0)
        * Maximum green score should have maximum affinity (100)
        """
        if metric not in ["carbon", "water", "green-score"]:
            raise Exception(f"Invalid metric: {metric}")

        # Affinity range
        af_min, af_max = 0, 100

        # Footprint range
        footprints = {}
        for k, v in datacenters.items():
            mean = statistics.mean([i[1] for i in self.metrics[k][metric]])
            # In the case of carbon/water, high PUE should increase the footprint
            if metric in ["carbon", "water"]:
                footprints[k] = mean * v["PUE"]
            # In the case of green score, high PUE should lower the score
            elif metric in ["green-score"]:
                footprints[k] = mean / v["PUE"]
        fp_min, fp_max = min(footprints.values()), max(footprints.values())

        # Compute affinity
        affinities = {}
        for k, x in footprints.items():
            if fp_min != fp_max:
                # Inverse linear mapping
                if metric in ["carbon", "water"]:
                    affinities[k] = round(
                        af_max + (af_min - af_max) * (x - fp_min) / (fp_max - fp_min)
                    )
                # Linear mapping
                elif metric in ["green-score"]:
                    affinities[k] = round(
                        af_min + (af_max - af_min) * (x - fp_min) / (fp_max - fp_min)
                    )
            else:  # avoid dividing by zero
                affinities[k] = 0

        return affinities

    def rank(self, subset: list = None):
        """
        Compute affinities for datacenter.
        We allow to specify a subset of datacenters, to account for the fact that
        each user only sees the datacenters belonging to their VO.
        """
        if subset is None or not subset:
            subset = self.datacenters.keys()

        datacenters = {k: v for k, v in self.datacenters.items() if k in subset}
        algorithm = getattr(self, f"_{self.algorithm}")
        return algorithm(datacenters)
