import ast
import copy
import csv
import os
from pathlib import Path
import types

from cachetools import cached, TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth
import ai4papi.conf as papiconf
import ai4papi.nomad.patches as npatches
import nomad

router = APIRouter(
    prefix="/stats",
    tags=["Deployments stats"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()
Nomad = nomad.Nomad()

main_dir = Path(__file__).resolve().parent

Nomad.job.get_allocations = types.MethodType(
    npatches.get_allocations,
    Nomad.job
)

def to_type(s):
        try:
            return ast.literal_eval(s)
        except Exception:
            return s
        

@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def load_stats(
    namespace: str,
    ):

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


def get_gpu_stats(
    nodes:any,
    stats: dict[str, dict]
    ):

    # Load GPU flavours
    gpu_flavours = {}

    with open(main_dir / 'gpu_flavors.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            flavor = row['Flavor']
            attrs = list(row.keys())[1:]
            gpu_flavours[flavor] = {k: to_type(row[k]) for k in attrs}

    # Compute total number of GPUs for each node
    for n in nodes:
        info = Nomad.node.get_node(n['ID'])
        flavour = info['Attributes'].get('platform.aws.instance-type', None)

        if flavour in gpu_flavours.keys():
            stats['nodes'][n['ID']]['gpu-total'] = int(gpu_flavours[flavour]['Number of GPUs'])

    return stats


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

    stats = {'nodes' : {}, 'cluster': {}}
    cpu_tot_cl = cpu_used_cl = gpu_tot_cl = gpu_used_cl = ram_tot_cl = ram_used_cl = disk_tot_cl = disk_used_cl = 0
    
    # Load nodes
    nodes = Nomad.nodes.get_nodes()

    # Get total stats for each node (except gpu)
    for n in nodes:
        node = Nomad.node.get_node(n['ID'])
        node_stats = {'cpu-total': int(node['Attributes']['cpu.numcores']),
                      'cpu-used': 0,
                      'gpu-total': 0,
                      'gpu-used': 0,
                      'ram-total': int(node['Attributes']['memory.totalbytes']),
                      'ram-used': 0,
                      'disk-total': int(node['Attributes']['unique.storage.bytestotal']),
                      'disk-used': int(node['Attributes']['unique.storage.bytesfree']),
                      }
        stats['nodes'][n['ID']] = node_stats

    # Get gpu stats
    stats = get_gpu_stats(nodes, stats)

    # Get aggregated usage stats for each node
    namespaces = ['default', 'ai4eosc', 'imagine', 'tutorials']

    for namespace in namespaces:
        jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_='Status == "running"')  # job summaries
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
            
            node_id = a['NodeID']

            # Add resources
            if 'usertask' in a['AllocatedResources']['Tasks']:
                res = a['AllocatedResources']['Tasks']['usertask']
                # cpu
                cpu = len(res['Cpu']['ReservedCores']) if res['Cpu']['ReservedCores']  else 0
                stats['nodes'][node_id]['cpu-used'] += cpu
                # ram
                ram = res['Memory']['MemoryMB']
                stats['nodes'][node_id]['ram-used'] += ram
                # gpu
                gpu = [d for d in res['Devices'] if d['Type'] == 'gpu'][0] if res['Devices'] else None
                gpu_num = len(gpu['DeviceIDs']) if gpu else 0
                stats['nodes'][node_id]['gpu-used'] += gpu_num
            else:
                continue

    # Compute cluster stats
    for n in stats['nodes']:
        cpu_tot_cl +=  stats['nodes'][n]['cpu-total']
        cpu_used_cl += stats['nodes'][n]['cpu-used']
        gpu_tot_cl += stats['nodes'][n]['gpu-total']
        gpu_used_cl += stats['nodes'][n]['gpu-used']
        ram_tot_cl += stats['nodes'][n]['ram-total']
        ram_used_cl += stats['nodes'][n]['ram-used']
        disk_tot_cl += stats['nodes'][n]['disk-total']
        disk_used_cl += stats['nodes'][n]['disk-used']
        
    stats['cluster'] = {'cpu-total': cpu_tot_cl, 
                      'cpu-used': cpu_used_cl,
                      'gpu-total': gpu_tot_cl,
                      'gpu-used': gpu_used_cl,
                      'ram-total': ram_tot_cl,
                      'ram-used': ram_used_cl,
                      'disk-total': disk_tot_cl,
                      'disk-used': disk_used_cl,
                      }

    return stats
