"""
Return stats from the user/VO/cluster
"""

import copy
import csv
import time
from datetime import datetime, timedelta
import os
from pathlib import Path

from cachetools import cached, TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth, schemas
from ai4papi.wattnet import green_director
import ai4papi.conf as papiconf
from ai4papi.nomad_utils import Nomad


router = APIRouter(
    prefix="/stats",
    tags=["Stats"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

main_dir = Path(__file__).resolve().parent

cluster_stats = None
cluster_stats_updated_at = None


@cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
def load_stats(
    namespace: str,
):
    """
    CSV reader and data filtering could be improved with Pandas, but that's a heavy
    dependency, so we're keeping it like this for the moment.
    """

    main_dir = os.environ.get("ACCOUNTING_PTH", None)
    if not main_dir:
        raise HTTPException(
            status_code=500,
            detail="Deployments stats information not available (no env var).",
        )

    # Load all stats files
    stats = {}
    for name in ["full-agg", "timeseries", "users-agg"]:
        pth = Path(main_dir) / "summaries" / f"{namespace}-{name}.csv"

        if not pth.is_file():
            raise HTTPException(
                status_code=500,
                detail="Deployments stats information not available (missing file).",
            )

        with open(pth, "r") as f:
            reader = csv.DictReader(f, delimiter=";")
            if not reader.fieldnames:
                raise ValueError("CSV is missing fieldnames")
            stats[name] = {k: [] for k in reader.fieldnames}
            for row in reader:
                for k, v in row.items():
                    if k not in ["date", "owner"]:
                        v = int(v)
                    stats[name][k].append(v)

    # In VO timeseries, only return last three months
    threshold = datetime.now() - timedelta(days=90)
    threshold = str(threshold.date())
    try:
        idx = [i > threshold for i in stats["timeseries"]["date"]].index(True)
    except Exception:
        # If there are no data in the last 90 days, then return last 90 dates
        idx = -90
    for k, v in stats["timeseries"].items():
        stats["timeseries"][k] = v[idx:]

    # Namespace aggregates are not lists
    stats["full-agg"] = {k: v[0] for k, v in stats["full-agg"].items()}

    return stats


@router.get("/user")
def get_user_stats(
    vo: str,
    authorization=Depends(security),
):
    """
    Returns the following stats (per resource type):
    * the time-series usage of that VO
    * the aggregated usage of that VO
    * the aggregated usage of the user in that VO

    Parameters:
    * **vo**: Virtual Organization where you want the stats from.
    """

    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info, vo)

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    # Load proper namespace stats
    full_stats = load_stats(namespace=namespace)

    # Keep only stats from the current user
    user_stats = copy.deepcopy(full_stats)
    try:
        idx = full_stats["users-agg"]["owner"].index(auth_info["id"])
        user_stats["users-agg"] = {
            k: v[idx] for k, v in full_stats["users-agg"].items()
        }
    except ValueError:  # user has still no recorded stats
        user_stats["users-agg"] = None

    return user_stats


def get_proper_allocation(allocs):
    # Reorder allocations based on recency
    dates = [a["CreateTime"] for a in allocs]
    allocs = [
        x
        for _, x in sorted(
            zip(dates, allocs),
            key=lambda pair: pair[0],
        )
    ][::-1]  # more recent first

    # Select the proper allocation
    statuses = [a["ClientStatus"] for a in allocs]
    if "unknown" in statuses:
        # The node has lost connection. Avoid showing temporary reallocated job,
        # to avoid confusions when the original allocation is restored back again.
        idx = statuses.index("unknown")
    elif "running" in statuses:
        # If an allocation is running, return that allocation
        # It happens that after a network cut, when the network is restored,
        # the temporary allocation created in the meantime (now with status
        # 'complete') is more recent than the original allocation that we
        # recovered (with status 'running'), so using only recency does not work.
        idx = statuses.index("running")
    else:
        # Return most recent allocation
        idx = 0

    return allocs[idx]["ID"]


@router.get("/cluster")
@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_cluster_stats(
    vo: str | None = None,
) -> schemas.ClusterStats:
    """
    Returns the following stats of the nodes and the cluster (per resource type):
    * the aggregated usage
    * the total capacity

    Parameters
    ----------
    vo: string
      Keep only the nodes supporting a specific VO. If not provided, returns all nodes.
    """

    global cluster_stats, cluster_stats_updated_at
    if not cluster_stats:
        # If PAPI is used as a package, cluster_stats will be None, as the background
        # computation of `get_cluster_stats_bg()` is only started when PAPI is launched
        # with uvicorn.
        # So if None, we need to initialize it
        cluster_stats = get_cluster_stats_bg()

    # If the background task fails for some reason (failed Nomad calls, failed WattNet
    # calls, etc), the stats won't be updated and this endpoint will keep serving the
    # same (old) stats, which can be misleading because it gives the impression that
    # everything works normally. So we give a 1 hour grace time and then raise an Error.
    if (time.time() - cluster_stats_updated_at) > 3600:  # ty: ignore[unsupported-operator]
        raise HTTPException(
            status_code=500,
            detail="Cluster stats have not been updated for more than 1 hour.",
        )

    stats = copy.deepcopy(cluster_stats)

    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo] if vo else "all"

    for k, v in list(stats.datacenters.items()):  # we make an object copy with list()
        # Filter out nodes that do not support the given VO
        nodes = {}
        for n_id, n_stats in v.nodes.items():
            if namespace == "all" or namespace in n_stats.namespaces:
                nodes[n_id] = n_stats

        # Ignore datacenters with no nodes
        if not nodes:
            del stats.datacenters[k]
        else:
            stats.datacenters[k].nodes = nodes

    # Reset cluster stats for clean aggregation
    stats.cluster = schemas.ResourceStats(gpu_models={})

    # Compute cluster stats after node filtering is done
    for dc_stats in stats.datacenters.values():
        for n_stats in dc_stats.nodes.values():
            for field in schemas.ResourceStats.model_fields:
                if field != "gpu_models":
                    setattr(
                        stats.cluster,
                        field,
                        getattr(stats.cluster, field) + getattr(n_stats, field),
                    )

            for model_name, g_stats in n_stats.gpu_models.items():
                if model_name not in stats.cluster.gpu_models:
                    stats.cluster.gpu_models[model_name] = (
                        schemas.ResourceStats.GpuModelStats(
                            gpu_total=0,
                            gpu_used=0,
                        )
                    )
                stats.cluster.gpu_models[model_name].gpu_total += g_stats.gpu_total
                stats.cluster.gpu_models[model_name].gpu_used += g_stats.gpu_used

    # Add update time
    stats.updated_at = (
        datetime.fromtimestamp(cluster_stats_updated_at).isoformat() + "Z"  # ty: ignore[invalid-argument-type]
    )

    return stats


@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_cluster_stats_bg() -> schemas.ClusterStats:
    """
    Background task that computes the stats of the nodes.
    The TTL of this task should be >= than the repeat frequency of the thread defined
    in main.py.
    """
    # Start from default datacenters dict
    datacenters_conf = copy.deepcopy(papiconf.datacenters)

    # Retrieve datacenter footprints
    green_director.retrieve_footprints()
    for dc_name, metrics in green_director.metrics.items():
        if dc_name in datacenters_conf:
            datacenters_conf[dc_name]["footprints"] = metrics

    # Instantiate datacenters dictionary containing schemas.DatacenterStats objects
    datacenters = {}
    for dc_name, dc in datacenters_conf.items():
        datacenters[dc_name] = schemas.DatacenterStats(
            lat=dc.get("lat", 0.0),
            lon=dc.get("lon", 0.0),
            PUE=dc.get("PUE", 0.0),
            nodes={},
            footprints=dc.get("footprints"),
        )

    # Init stats using schemas.ClusterStats
    stats = schemas.ClusterStats(
        datacenters=datacenters,
        cluster=schemas.ResourceStats(gpu_models={}),
    )

    # Load nodes
    nodes = Nomad.nodes.get_nodes(resources=True)
    nodes_dc = {}  # dict(node, datacenter)

    # Get total stats for each node
    for n in nodes:
        node = Nomad.node.get_node(n["ID"])

        # Sometimes nodes disconnect. And since they disconnect, their metadata cannot
        # longer be updated. So we use the fine-grained status in the metadata *only if*
        # the node status is ready.
        status = (
            node["Meta"].get("status", "")
            if node["Status"] == "ready"
            else node["Status"]
        )

        # Track stats per GPU model type
        gpu_total = 0
        gpu_models = {}
        if n["NodeResources"]["Devices"]:
            for devices in n["NodeResources"]["Devices"]:
                if devices["Type"] == "gpu":
                    gpu_total += len(devices["Instances"])

                    if devices["Name"] not in gpu_models:
                        gpu_models[devices["Name"]] = (
                            schemas.ResourceStats.GpuModelStats(
                                gpu_total=0,
                                gpu_used=0,
                            )
                        )

                    gpu_models[devices["Name"]].gpu_total += len(devices["Instances"])

        # If datacenter is not in csv, load default info
        if n["Datacenter"] not in stats.datacenters:
            stats.datacenters[n["Datacenter"]] = schemas.DatacenterStats(
                lat=0.0,
                lon=0.0,
                PUE=0.0,
                nodes={},
            )
            print(
                f"Warning: Datacenter {n['Datacenter']} not found in datacenters.csv file"
            )

        n_stats = schemas.NodeInfo(
            name=node["Name"],
            eligibility=node["SchedulingEligibility"],
            namespaces=node["Meta"].get("namespace", ""),
            type=node["Meta"].get("type", ""),
            status=status,
            tags=node["Meta"].get("tags", ""),
            cpu_model=node["Attributes"].get("cpu.modelname", ""),
            cpu_total=int(node["Attributes"]["cpu.numcores"]),
            ram_total=int(node["Attributes"]["memory.totalbytes"]) / 2**20,
            disk_total=int(node["Attributes"]["unique.storage.bytestotal"]) / 2**20,
            gpu_total=gpu_total,
            gpu_models=gpu_models,
        )

        stats.datacenters[n["Datacenter"]].nodes[n["ID"]] = n_stats
        nodes_dc[n["ID"]] = n["Datacenter"]

    # Get aggregated usage stats for each node
    namespaces_list = ["default"] + list(
        papiconf.MAIN_CONF["nomad"]["namespaces"].values()
    )

    for ns in namespaces_list:
        jobs = Nomad.jobs.get_jobs(namespace=ns, filter_='Status == "running"')
        for j in jobs:
            # Retrieve full job for meta
            job = Nomad.job.get_job(id_=j["ID"], namespace=ns)

            # Keep the proper allocation
            allocs = Nomad.job.get_allocations(id_=job["ID"], namespace=ns)
            a = Nomad.allocation.get_allocation(get_proper_allocation(allocs))

            # Add resources
            datacenter = nodes_dc[a["NodeID"]]
            n_stats = stats.datacenters[datacenter].nodes[a["NodeID"]]

            # TODO: we are ignoring resources consumed by other jobs
            if not job["Name"].startswith(("module", "tool")):
                continue

            n_stats.jobs_num += 1

            # TODO: we are ignoring resources consumed by other tasks
            if "main" in a["AllocatedResources"]["Tasks"]:
                res = a["AllocatedResources"]["Tasks"]["main"]

                # cpu
                if res["Cpu"]["ReservedCores"]:
                    n_stats.cpu_used += len(res["Cpu"]["ReservedCores"])

                # ram
                n_stats.ram_used += res["Memory"]["MemoryMB"]

                # disk
                # Note: In theory we can get the total disk used in a node looking at the
                # metadata (ie. "unique.storage.bytesfree"). But that gave us the disk that
                # is actually used. But we are instead interested on the disk that is reserved
                # by users (regardless of whether they are actually using it).
                n_stats.disk_used += a["AllocatedResources"]["Shared"]["DiskMB"]

                # gpu
                if res["Devices"]:
                    gpu = [d for d in res["Devices"] if d["Type"] == "gpu"][0]
                    gpu_num = len(gpu["DeviceIDs"]) if gpu else 0

                    # Sometimes the node fails and GPUs are not detected [1].
                    # In that case, avoid counting that GPU in the stats.
                    # [1]: https://docs.ai4os.eu/en/latest/user/others/faq.html#my-gpu-just-disappeared-from-my-deployment
                    if n_stats.gpu_models:
                        n_stats.gpu_used += gpu_num
                        if gpu["Name"] not in n_stats.gpu_models:
                            n_stats.gpu_models[gpu["Name"]] = (
                                schemas.ResourceStats.GpuModelStats(
                                    gpu_total=0, gpu_used=0
                                )
                            )
                        n_stats.gpu_models[gpu["Name"]].gpu_used += gpu_num

            # We also want to keep track of how many allocations in a node were reallocated
            # (frequent reallocation is a sign of node malfunctioning)
            if len(allocs) > 1:  # the job has been reallocated
                for a_alloc in allocs:
                    if a_alloc["NextAllocation"]:  # this alloc has been reallocated
                        datacenter = nodes_dc[a_alloc["NodeID"]]
                        n_stats = stats.datacenters[datacenter].nodes[a_alloc["NodeID"]]
                        n_stats.reallocations += 1

    # Keep ineligible nodes, but set (used=total) for all resources
    # We don't remove the node altogether because jobs might still be running there
    # and we want to show them in the stats
    for datacenter in stats.datacenters.values():
        for n_stats in datacenter.nodes.values():
            if n_stats.eligibility == "ineligible":
                for r in ["cpu", "gpu", "ram", "disk"]:
                    setattr(n_stats, f"{r}_total", getattr(n_stats, f"{r}_used"))
                for g_stats in n_stats.gpu_models.values():
                    g_stats.gpu_total = n_stats.gpu_used

    # Set the new shared variable
    global cluster_stats, cluster_stats_updated_at
    cluster_stats = stats
    cluster_stats_updated_at = time.time()

    return cluster_stats
