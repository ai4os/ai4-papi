"""
Utilities for the integration with WattNet.
API reference: https://api.wattnet.eu/v1/docs
"""

import datetime
import json
import os
import statistics
import warnings
from collections.abc import Iterable

import requests
from cachetools import TTLCache, cached

import ai4papi.conf as papiconf
from ai4papi import utils

session = requests.Session()

WATTNET_URL = "https://api.wattnet.eu"
WATTNET_EMAIL = "bot@ai4eosc.eu"
WATTNET_PASS = os.environ.get("WATTNET_PASSWORD")
if not WATTNET_PASS:
    print("You should define a WATTNET_PASSWORD")


def algorithm(func):
    """Decorator to mark a method as a ranking algorithm."""
    func._is_algorithm = True
    return func


class GreenDirector:
    # Define sensible default footprint values for datacenter outside WattNet scope (Europe)
    DEFAULTS = {
        "carbon": 301,  # default energy quality in gCO2/kWh
        "water": 12,  # default water usage in L/kWh
        "green-score": 50,  # default green score (combining carbon and water).
    }

    def __init__(self, algorithm: str = "linear_rank"):
        """
        Green metrics are saved in the metrics var.

        Parameters
        ----------
        algorithm : str
            Name of the ranking algorithm to use.
        """
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
        self.algorithm_name = algorithm
        self.metrics = {}

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
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(days=7)
        params = {
            "lat": lat,
            "lon": lon,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
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
        if (not footprints) or (not score):
            raise Exception("Error retrieving information from Wattnet.")
        score[0].update({"footprint_type": "green-score"})

        return footprints + score

    def retrieve_footprints(self):
        """
        Retrieve the footprints for all datacenters.
        If we are unable to retrieve a value (e.g. because location is outside Europe),
        we return a reasonable default value.
        """
        # We don't retrieve the datacenters from stats dict because this function might
        # be called before stats have been initialized
        datacenters = papiconf.datacenters

        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(days=7)

        for k, v in datacenters.items():
            data = self._fetch_footprint_data(v["lat"], v["lon"])

            # For each footprint type, we concatenate all timeseries, irrespectively of
            # whether they are [valid=True] (meaning their values are final) or
            # [valid=False] (meaning that they are an estimation, subject to change)
            self.metrics[k] = {}
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

    def normalize_list(
        self,
        values: Iterable[float],
        target_range: tuple[float, float] = (0.0, 1.0),
    ) -> list[float]:
        """
        Normalizes a list of numerical values to a specified target range.
        """
        if not values:
            return []

        val_min, val_max = min(values), max(values)
        target_min, target_max = target_range

        # Handle the edge case where all values in the list are identical
        if val_min == val_max:
            return [target_min] * len(values)

        range_span = val_max - val_min
        target_span = target_max - target_min

        return [target_min + ((x - val_min) * target_span) / range_span for x in values]

    @algorithm
    def _linear_rank(self, stats: dict, metric: str = "green-score"):
        """
        Computes a DIRAC-like score for each node:

          score = processor_efficiency / (datacenter_PUE * datacenter_footprint)
        """
        metrics = ["carbon", "water", "green-score"]
        if metric not in metrics:
            raise ValueError(f"Invalid metric: {metric}. Must be one of: {metrics}")

        # Retrieve CPU & GPU efficiencies
        cpu_specs, gpu_specs = utils.cpu_specs(), utils.gpu_specs()
        cpu_eff, gpu_eff = {}, {}
        for dc_name, dc in stats.datacenters.items():
            for nid, node in dc.nodes.items():
                if node.type not in ["compute", "batch"]:
                    # We don't want to include traefik or try-me nodes
                    continue

                # CPU
                cloud = dc_name.split("-")[0]  # e.g. "ifca-ai4eosc" --> "ifca"
                cpu_model = (node.cpu_model, cloud)
                if cpu_model not in cpu_specs.keys():
                    print(f"Missing CPU model {cpu_model} from `cpu_models.csv`")
                    continue
                cpu = cpu_specs[(node.cpu_model, cloud)]
                cpu_eff[nid] = cpu["norm_factor"] / cpu["tdp_per_core"]

                # GPU
                if node.gpu_total <= 0:
                    continue
                gpu_model = list(node.gpu_models.keys())[0]
                if gpu_model not in gpu_specs.keys():
                    print(f"Missing GPU model {gpu_model} from `gpu_models.csv`")
                    continue
                gpu = gpu_specs[gpu_model]
                gpu_eff[nid] = gpu["tflops_fp32"] / gpu["tdp"]

        # Normalize the efficiencies to the range [1, 2] so that we can combine them
        # when needed (e.g. in GPU workloads)
        scaled_values = self.normalize_list(cpu_eff.values(), target_range=(1, 2))
        cpu_eff = dict(zip(cpu_eff.keys(), scaled_values))
        scaled_values = self.normalize_list(gpu_eff.values(), target_range=(1, 2))
        gpu_eff = dict(zip(gpu_eff.keys(), scaled_values))

        # Compute the individual node scores (the higher the better)
        scores = {}
        for dc_name, dc in stats.datacenters.items():
            # Compute the overall datacenter footprint
            mean = statistics.mean([i[1] for i in self.metrics[dc_name][metric]])
            # In the case of green score, high values lower the footprint
            mean = 1 / mean if metric in ["green-score"] else mean

            # Now compute per node Dirac-style green-score, weighting the datacenter
            # footprint with the node specific efficiency
            for nid, node in dc.nodes.items():
                if nid not in cpu_eff.keys():
                    continue
                scores[nid] = {}
                scores[nid]["cpu"] = cpu_eff[nid] / (mean * dc.PUE)

                if nid not in gpu_eff.keys():
                    continue
                # If workload combines CPUs and GPUs, we weight them with a scaling factor.
                # We *assume* that a typical GPU workload consumes 80% of its power on
                # the GPU and 20% on a CPU
                combined_eff = 0.2 * cpu_eff[nid] + 0.8 * gpu_eff[nid]
                scores[nid]["gpu"] = combined_eff / (mean * dc.PUE)

        return scores

    def rank(self, stats: dict):
        """
        Compute affinities for each node.
        Stats only have the node information of the nodes belonging to a specific VO.
        """
        algorithm_func = getattr(self, f"_{self.algorithm_name}")
        return algorithm_func(stats)

    def add_green_affinities(
        self,
        nomad_conf: dict,
        stats: dict,
        workload_type: str = "cpu",
    ):
        """
        Add affinities for greener datacenters to a Nomad job.
        We add one affinity per node.
        """
        scores = self.rank(stats)
        scores = {
            k: v[workload_type] for k, v in scores.items() if workload_type in v.keys()
        }

        # Rescale scores to range [0, 30] to avoid interfering too much
        # with other constraints/affinities.
        scaled_values = self.normalize_list(scores.values(), target_range=(0, 30))
        scores = dict(zip(scores.keys(), scaled_values))

        for nid, affinity in scores.items():
            # Nomad Nomad requires non-zero int affinities
            affinity = int(affinity)
            if affinity == 0:
                continue

            nomad_conf["Affinities"].append(
                {
                    "LTarget": "${node.unique.id}",
                    "Operand": "=",
                    "RTarget": nid,
                    "Weight": affinity,
                }
            )

        return nomad_conf


# Init here so that the same instantiation can be shared by different modules
green_director = GreenDirector()
