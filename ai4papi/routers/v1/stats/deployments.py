"""
Return stats from the user/VO/cluster
"""

import copy
import csv
from datetime import datetime, timedelta
import os
from pathlib import Path
import types

from cachetools import cached, TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
import nomad

from ai4papi import auth
import ai4papi.conf as papiconf
import ai4papi.nomad.patches as npatches


router = APIRouter(
    prefix="/stats",
    tags=["Deployments stats"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

main_dir = Path(__file__).resolve().parent

Nomad = nomad.Nomad()
Nomad.job.get_allocations = types.MethodType(npatches.get_allocations, Nomad.job)

cluster_stats = None


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
    auth.check_vo_membership(vo, auth_info["vos"])

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


@cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
def load_datacenters():
    # Check if datacenter info file is available
    pth = papiconf.main_path.parent / "var" / "datacenters_info.csv"
    if not pth.is_file():
        return {}

    # Load datacenter info
    datacenters = {}
    with open(pth, "r") as f:
        reader = csv.DictReader(f, delimiter=";")
        dc_keys = reader.fieldnames.copy()
        dc_keys.remove("name")
        for row in reader:
            for k, v in row.items():
                if k == "name":
                    name = v
                    datacenters[name] = {k: 0 for k in dc_keys}
                    datacenters[name]["nodes"] = {}
                else:
                    datacenters[name][k] = float(v)

    return datacenters


@router.get("/cluster")
@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_cluster_stats(
    vo: str,
):
    """
    Returns the following stats of the nodes and the cluster (per resource type):
    * the aggregated usage
    * the total capacity
    """

    global cluster_stats
    if not cluster_stats:
        # If PAPI is used as a package, cluster_stats will be None, as the background
        # computation of `get_cluster_stats_bg()` is only started when PAPI is launched
        # with uvicorn.
        # So if None, we need to initialize it
        cluster_stats = get_cluster_stats_bg()
    stats = copy.deepcopy(cluster_stats)

    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    for k, v in stats["datacenters"].copy().items():
        # Filter out nodes that do not support the given VO
        nodes = {}
        for n_id, n_stats in v["nodes"].items():
            if namespace in n_stats["namespaces"]:
                nodes[n_id] = n_stats

        # Ignore datacenters with no nodes
        if not nodes:
            del stats["datacenters"][k]
        else:
            stats["datacenters"][k]["nodes"] = nodes

    # Compute cluster stats after node filtering is done
    for dc_stats in stats["datacenters"].values():
        for n_stats in dc_stats["nodes"].values():
            for k, v in n_stats.items():
                # Ignore keys
                if k in ["name", "namespaces", "eligibility", "status", "tags"]:
                    continue

                # Aggregate nested gpu_models dict
                elif k == "gpu_models":
                    for k1, v1 in v.items():
                        model_stats = stats["cluster"]["gpu_models"].get(
                            k1,
                            {
                                "gpu_total": 0,
                                "gpu_used": 0,
                            },  # init value
                        )
                        for k2, v2 in v1.items():
                            model_stats[k2] += v2
                        stats["cluster"]["gpu_models"][k1] = model_stats

                # Aggregate other resources
                else:
                    stats["cluster"][k] += v

    return stats


@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_cluster_stats_bg():
    """
    Background task that computes the stats of the nodes.
    The TTL of this task should be >= than the repeat frequency of the thread defined
    in main.py.
    """

    resources = [
        "jobs_num",
        "cpu_total",
        "cpu_used",
        "gpu_total",
        "gpu_used",
        "ram_total",
        "ram_used",
        "disk_total",
        "disk_used",
    ]
    datacenters = load_datacenters()  # available datacenters info
    stats = {
        "datacenters": datacenters,  # aggregated datacenter usage
        "cluster": {k: 0 for k in resources},  # aggregated cluster usage
    }
    stats["cluster"]["gpu_models"] = {}

    # Load nodes
    nodes = Nomad.nodes.get_nodes(resources=True)
    nodes_dc = {}  # dict(node, datacenter)

    # Get total stats for each node
    for n in nodes:
        node = Nomad.node.get_node(n["ID"])
        n_stats = {k: 0 for k in resources}
        n_stats["name"] = node["Name"]
        n_stats["eligibility"] = node["SchedulingEligibility"]
        n_stats["cpu_total"] = int(node["Attributes"]["cpu.numcores"])
        n_stats["ram_total"] = int(node["Attributes"]["memory.totalbytes"]) / 2**20
        n_stats["disk_total"] = (
            int(node["Attributes"]["unique.storage.bytestotal"]) / 2**20
        )
        n_stats["gpu_models"] = {}
        n_stats["namespaces"] = node["Meta"].get("namespace", "")
        n_stats["status"] = node["Meta"].get("status", "")
        n_stats["tags"] = node["Meta"].get("tags", "")

        if n["NodeResources"]["Devices"]:
            for devices in n["NodeResources"]["Devices"]:
                if devices["Type"] == "gpu":
                    n_stats["gpu_total"] += len(devices["Instances"])

                    # Track stats per GPU model type
                    if devices["Name"] not in n_stats["gpu_models"].keys():
                        n_stats["gpu_models"][devices["Name"]] = {
                            "gpu_total": 0,
                            "gpu_used": 0,
                        }

                    n_stats["gpu_models"][devices["Name"]]["gpu_total"] += len(
                        devices["Instances"]
                    )

        # If datacenter is not in csv, load default info
        if n["Datacenter"] not in stats["datacenters"]:
            stats["datacenters"][n["Datacenter"]] = {
                "lat": 0,
                "lon": 0,
                "PUE": 0,
                "energy_quality": 0,
                "nodes": {},
            }

        stats["datacenters"][n["Datacenter"]]["nodes"][n["ID"]] = n_stats
        nodes_dc[n["ID"]] = n["Datacenter"]

    # Get aggregated usage stats for each node
    namespaces = ["default"] + list(papiconf.MAIN_CONF["nomad"]["namespaces"].values())

    for namespace in namespaces:
        jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_='Status == "running"')
        for j in jobs:
            # Retrieve full job for meta
            job = Nomad.job.get_job(
                id_=j["ID"],
                namespace=namespace,
            )

            allocs = Nomad.job.get_allocations(
                id_=job["ID"],
                namespace=namespace,
            )

            # Keep the proper allocation
            a = Nomad.allocation.get_allocation(get_proper_allocation(allocs))

            # Add resources
            datacenter = nodes_dc[a["NodeID"]]
            n_stats = stats["datacenters"][datacenter]["nodes"][a["NodeID"]]

            # TODO: we are ignoring resources consumed by other jobs
            if job["Name"].startswith("module") or job["Name"].startswith("tool"):
                n_stats["jobs_num"] += 1

            # TODO: we are ignoring resources consumed by other tasks
            if "main" in a["AllocatedResources"]["Tasks"]:
                res = a["AllocatedResources"]["Tasks"]["main"]

                # cpu
                if res["Cpu"]["ReservedCores"]:
                    n_stats["cpu_used"] += len(res["Cpu"]["ReservedCores"])

                # ram
                n_stats["ram_used"] += res["Memory"]["MemoryMB"]

                # disk
                # Note: In theory we can get the total disk used in a node looking at the
                # metadata (ie. "unique.storage.bytesfree"). But that gave us the disk that
                # is actually used. But we are instead interested on the disk that is reserved
                # by users (regardless of whether they are actually using it).
                n_stats["disk_used"] += a["AllocatedResources"]["Shared"]["DiskMB"]

                # gpu
                if res["Devices"]:
                    gpu = [d for d in res["Devices"] if d["Type"] == "gpu"][0]
                    gpu_num = len(gpu["DeviceIDs"]) if gpu else 0

                    # Sometimes the node fails and GPUs are not detected [1].
                    # In that case, avoid counting that GPU in the stats.
                    # [1]: https://docs.ai4os.eu/en/latest/user/others/faq.html#my-gpu-just-disappeared-from-my-deployment
                    if n_stats["gpu_models"]:
                        n_stats["gpu_used"] += gpu_num
                        n_stats["gpu_models"][gpu["Name"]]["gpu_used"] += gpu_num
            else:
                continue

    # Keep ineligible nodes, but set (used=total) for all resources
    # We don't remove the node altogether because jobs might still be running there
    # and we want to show them in the stats
    for datacenter in stats["datacenters"].values():
        for n_stats in datacenter["nodes"].values():
            if n_stats["eligibility"] == "ineligible":
                for r in ["cpu", "gpu", "ram", "disk"]:
                    n_stats[f"{r}_total"] = n_stats[f"{r}_used"]
                for g_stats in n_stats["gpu_models"].values():
                    g_stats["gpu_total"] = n_stats["gpu_used"]

    # Set the new shared variable
    global cluster_stats
    cluster_stats = stats

    return cluster_stats
