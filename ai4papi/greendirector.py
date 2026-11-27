"""
GreenDirector: ranks the nodes by how green they are (Wattnet footprints of their
datacenter and hardware efficiency) and adds the matching Nomad affinities.
"""

import datetime
import logging
import statistics
from collections.abc import Iterable

from cachetools import TTLCache

import ai4papi.conf as papiconf
from ai4papi import utils
from ai4papi.clients import wattnet

LOG = logging.getLogger(__name__)

FAILURE_BACKOFF = 5 * 60  # seconds a failing datacenter is not asked again


def algorithm(func):
    """Decorator to mark a method as a ranking algorithm."""
    func._is_algorithm = True
    return func


class GreenDirector:
    # Define sensible default footprint values for datacenter outside Wattnet scope (Europe)
    DEFAULTS = {
        "carbon": 301,  # default energy quality in gCO2/kWh
        "water": 12,  # default water usage in L/kWh
        "environmental-score": 50,  # default environmental score (combining carbon and water).
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
        self._backoff = TTLCache(maxsize=1024, ttl=FAILURE_BACKOFF)
        self._failing = set()  # datacenters whose last refresh failed (log on change)

    def retrieve_metrics(self):
        """
        Retrieve the footprints for all datacenters.
        If we are unable to retrieve a value (e.g. because location is outside Europe),
        we return a reasonable default value.
        """
        # We don't retrieve the datacenters from stats dict because this function might
        # be called before stats have been initialized
        datacenters = papiconf.datacenters

        end = datetime.datetime.now(datetime.UTC)
        start = end - datetime.timedelta(days=7)

        for k in datacenters:
            if k in self._backoff:
                continue  # failed a moment ago: do not wait on a down Wattnet again
            try:
                data = wattnet.latest_metrics(k)
            except Exception as e:
                self._backoff[k] = True
                if k not in self._failing:
                    self._failing.add(k)
                    kept = self.metrics.get(k, {}).get("carbon")
                    LOG.warning(
                        "Wattnet refresh failed for %s (%s); %s",
                        k,
                        e,
                        f"keeping data up to {kept[-1][0]}"
                        if kept
                        else "using default values",
                    )
                if k in self.metrics:
                    continue  # keep the last good data rather than overwrite it
                data = {}  # never had data: fall back to DEFAULTS
            else:
                if k in self._failing:
                    self._failing.discard(k)
                    LOG.info("Wattnet refresh recovered for %s", k)

            self.metrics[k] = dict(data)

            # For datacenters outside Europe, Wattnet offers no data
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
    def _linear_rank(self, stats: dict, metric: str = "environmental-score"):
        """
        Computes a DIRAC-like green_score for each node:

          green_score = processor_efficiency / (datacenter_PUE * datacenter_footprint)
        """
        metrics = ["carbon", "water", "environmental-score"]
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

        # Compute the individual node green_scores (the higher the better)
        green_scores = {}
        for dc_name, dc in stats.datacenters.items():
            # Compute the overall datacenter footprint
            mean = statistics.mean([i[1] for i in self.metrics[dc_name][metric]])
            # In the case of environmental score, high values lower the footprint
            mean = 1 / mean if metric in ["environmental-score"] else mean

            # Now compute per node Dirac-style environmental-score, weighting the datacenter
            # footprint with the node specific efficiency
            for nid, node in dc.nodes.items():
                if nid not in cpu_eff.keys():
                    continue
                green_scores[nid] = {}
                green_scores[nid]["cpu"] = cpu_eff[nid] / (mean * dc.PUE)

                if nid not in gpu_eff.keys():
                    continue
                # If workload combines CPUs and GPUs, we weight them with a scaling factor.
                # We *assume* that a typical GPU workload consumes 80% of its power on
                # the GPU and 20% on a CPU
                combined_eff = 0.2 * cpu_eff[nid] + 0.8 * gpu_eff[nid]
                green_scores[nid]["gpu"] = combined_eff / (mean * dc.PUE)

        return green_scores

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
