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
Nomad.job.get_allocations = types.MethodType(
    npatches.get_allocations,
    Nomad.job
)

cluster_stats = None


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def load_stats(
    namespace: str,
    ):
    """
    CSV reader and data filtering could be improved with Pandas, but that's a heavy
    dependency, so we're keeping it like this for the moment.
    """

    main_dir = os.environ.get('ACCOUNTING_PTH', None)
    if not main_dir:
        raise HTTPException(
            status_code=500,
            detail="Deployments stats information not available (no env var).",
            )

    # Load all stats files
    stats = {}
    for name in ['full-agg', 'timeseries', 'users-agg']:
        pth = Path(main_dir) / 'summaries' / f'{namespace}-{name}.csv'

        if not pth.is_file():
            raise HTTPException(
                status_code=500,
                detail="Deployments stats information not available (missing file).",
                )

        with open(pth, 'r') as f:
            reader = csv.DictReader(f, delimiter=';')
            stats[name] = {k: [] for k in reader.fieldnames}
            for row in reader:
                for k, v in row.items():
                    if k not in ['date', 'owner']:
                        v= int(v)
                    stats[name][k].append(v)

    # In VO timeseries, only return last three months
    threshold = datetime.now() - timedelta(days=90)
    threshold = str(threshold.date())
    try:
        idx = [i > threshold for i in stats['timeseries']['date']].index(True)
    except Exception:
        # If there are no data in the last 90 days, then return last 90 dates
        idx = -90
    for k, v in stats['timeseries'].items():
        stats['timeseries'][k] = v[idx:]

    # Namespace aggregates are not lists
    stats['full-agg'] = {k: v[0] for k, v in stats['full-agg'].items()}

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
    auth.check_vo_membership(vo, auth_info['vos'])

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF['nomad']['namespaces'][vo]

    # Load proper namespace stats
    full_stats = load_stats(namespace=namespace)

    # Keep only stats from the current user
    user_stats = copy.deepcopy(full_stats)
    try:
        idx = full_stats['users-agg']['owner'].index(auth_info['id'])
        user_stats['users-agg'] = {k: v[idx] for k, v in full_stats['users-agg'].items()}
    except ValueError:  # user has still no recorded stats
        user_stats['users-agg'] = None

    return user_stats


def get_proper_allocation(allocs):

        # Reorder allocations based on recency
        dates = [a['CreateTime'] for a in allocs]
        allocs = [x for _, x in sorted(
            zip(dates, allocs),
            key=lambda pair: pair[0],
            )][::-1]  # more recent first

        # Select the proper allocation
        statuses = [a['ClientStatus'] for a in allocs]
        if 'unknown' in statuses:
            # The node has lost connection. Avoid showing temporary reallocated job,
            # to avoid confusions when the original allocation is restored back again.
            idx = statuses.index('unknown')
        elif 'running' in statuses:
            # If an allocation is running, return that allocation
            # It happens that after a network cut, when the network is restored,
            # the temporary allocation created in the meantime (now with status
            # 'complete') is more recent than the original allocation that we
            # recovered (with status 'running'), so using only recency does not work.
            idx = statuses.index('running')
        else:
            # Return most recent allocation
            idx = 0

        return allocs[idx]['ID']


@router.get("/cluster")
def get_cluster_stats():
    """
    Returns the following stats of the nodes and the cluster (per resource type):
    * the aggregated usage
    * the total capacity
    """

    global cluster_stats
    return cluster_stats


@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_cluster_stats_bg():
    """
    Background task that computes the stats of the nodes.
    The TTL of this task should be >= than the repeat frequency of the thread defined
    in main.py.
    """

    resources = [
        'cpu_total',
        'cpu_used',
        'gpu_total',
        'gpu_used',
        'ram_total',
        'ram_used',
        'disk_total',
        'disk_used',
    ]
    stats = {
        'nodes' : {},  # individual node usage
        'cluster': {k: 0 for k in resources},  # aggregated cluster usage
        }
    stats['cluster']['gpu_per_model'] = []

    # Load nodes
    nodes = Nomad.nodes.get_nodes(resources=True)
    gpu_stats = {}

    # Get total stats for each node
    for n in nodes:
        node = Nomad.node.get_node(n['ID'])

        n_stats = {k: 0 for k in resources}

        n_stats['name'] = node['Name']
        n_stats['cpu_total'] = int(node['Attributes']['cpu.numcores'])
        n_stats['ram_total'] = int(node['Attributes']['memory.totalbytes']) / 2**20
        n_stats['disk_total'] = int(node['Attributes']['unique.storage.bytestotal']) / 2**20
        n_stats['disk_used'] = int(node['Attributes']['unique.storage.bytesfree']) / 2**20
        if n['NodeResources']['Devices']:
            for devices in n['NodeResources']['Devices']:
                if devices['Type'] == 'gpu':
                    n_stats['gpu_total'] += len(devices['Instances'])
                    if (gpu_stats.get(devices['Name']) == None):
                        gpu_stats[devices['Name']] = {'gpu_total': 0, 'gpu_used': 0}
                    gpu_stats[devices['Name']]['gpu_total'] += len(devices['Instances'])
                   
        stats['nodes'][n['ID']] = n_stats

    # Get aggregated usage stats for each node
    namespaces = ['default', 'ai4eosc', 'imagine', 'tutorials']

    for namespace in namespaces:
        jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_='Status == "running"')
        for j in jobs:

            # Retrieve full job for meta
            job = Nomad.job.get_job(
                id_=j['ID'],
                namespace=namespace,
                )

            allocs = Nomad.job.get_allocations(
                id_=job['ID'],
                namespace=namespace,
                )

            # Keep the proper allocation
            a = Nomad.allocation.get_allocation(
                get_proper_allocation(allocs)
                )

            # Add resources
            n_stats = stats['nodes'][a['NodeID']]
            #FIXME: we are ignoring resources consumed by other tasks
            if 'usertask' in a['AllocatedResources']['Tasks']:
                res = a['AllocatedResources']['Tasks']['usertask']

                # cpu
                if res['Cpu']['ReservedCores']:
                    n_stats['cpu_used'] += len(res['Cpu']['ReservedCores'])

                # ram
                n_stats['ram_used'] += res['Memory']['MemoryMB']

                # gpu
                if res['Devices']:
                    gpu = [d for d in res['Devices'] if d['Type'] == 'gpu'][0]
                    gpu_num = len(gpu['DeviceIDs']) if gpu else 0
                    n_stats['gpu_used'] += gpu_num
                    gpu_stats[gpu['Name']]['gpu_used'] += gpu_num
            else:
                continue

    # Compute cluster stats
    for n_stats in stats['nodes'].values():
        for k, v in n_stats.items():
            if k != 'name' :
                stats['cluster'][k] += v

    stats['cluster']['gpu_per_model'] = gpu_stats

    # Set the new shared variable
    global cluster_stats
    cluster_stats = stats

    return cluster_stats
