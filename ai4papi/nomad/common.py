"""
Manage deployments with Nomad.

Notes:
=====
* Terminology warning: what we call a "deployment" (as in `create_deployment`)
 is a Nomad "job" (not a Nomad "deployment"!)
"""

from datetime import datetime
import re
import types

from cachetools import cached, TTLCache
from fastapi import HTTPException
import nomad
from nomad.api import exceptions
import requests

import ai4papi.conf as papiconf
import ai4papi.nomad.patches as nomad_patches


Nomad = nomad.Nomad()
# TODO: Remove monkey-patches when the code is merged to python-nomad Pypi package
Nomad.job.deregister_job = types.MethodType(
    nomad_patches.deregister_job,
    Nomad.job
    )
Nomad.job.get_allocations = types.MethodType(
    nomad_patches.get_allocations,
    Nomad.job
    )
Nomad.job.get_evaluations = types.MethodType(
    nomad_patches.get_allocations,
    Nomad.job
    )

# Persistent requests session for faster requests
session = requests.Session()


def get_deployments(
    namespace: str,
    owner: str,
    prefix: str = "",
    ):
    """
    Returns a list of all deployments belonging to a user, in a given namespace.
    """
    job_filter = \
        'Status != "dead" and ' + \
        f'Name matches "^{prefix}" and ' + \
        'Meta is not empty and ' + \
        f'Meta.owner == "{owner}"'
    jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_=job_filter)
    return jobs


def get_deployment(
    deployment_uuid: str,
    namespace: str,
    owner: str,
    full_info: True,
    ):
    """
    Retrieve the info of a specific deployment.
    Format outputs to a Nomad-independent format to be used by the Dashboard

    Parameters:
    * **vo**: Virtual Organization from where you want to retrieve your deployment
    * **deployment_uuid**: uuid of deployment to gather info about
    * **full_info**: retrieve the full information of that deployment (may increase
      latency)

    Returns a dict with info
    """
    # Check the deployment exists
    try:
        j = Nomad.job.get_job(
            id_=deployment_uuid,
            namespace=namespace,
            )
    except exceptions.URLNotFoundNomadException:
        raise HTTPException(
            status_code=400,
            detail="No deployment exists with this uuid.",
            )

    # Check job does belong to owner
    if j['Meta'] and owner != j['Meta'].get('owner', ''):
        raise HTTPException(
            status_code=400,
            detail="You are not the owner of that deployment.",
            )

    # Create job info dict
    info = {
        'job_ID': j['ID'],
        'name': j['Name'],
        'status': '',  # do not use j['Status'] as misleading
        'owner': j['Meta']['owner'],
        'title': j['Meta']['title'],
        'description': j['Meta']['description'],
        'docker_image': None,
        'docker_command': None,
        'submit_time': datetime.fromtimestamp(
            j['SubmitTime'] // 1000000000
        ).strftime('%Y-%m-%d %H:%M:%S'),  # nanoseconds to timestamp
        'resources': {},
        'endpoints': {},
        'active_endpoints': None,
        'main_endpoint': None,
        'alloc_ID': None,
        'datacenter': None,
    }

    # Retrieve tasks
    tasks = j['TaskGroups'][0]['Tasks']
    usertask = [t for t in tasks if t['Name'] == 'main'][0]

    # Retrieve Docker image
    info['docker_image'] = usertask['Config']['image']
    command = usertask['Config'].get('command', '')
    args = usertask['Config'].get('args', [])
    info['docker_command'] = f"{command} {' '.join(args)}".strip()

    # Add endpoints
    info['endpoints'] = {}
    for s in j['TaskGroups'][0]['Services']:
        label = s['PortLabel']

        # Iterate through tags to find `Host` tag
        for t in s['Tags']:
            try:
                url = re.search(r'Host\(`(.+?)`', t).group(1)
                break
            except Exception:
                url = "missing-endpoint"

        # Old deployments had network ports with names [deepaas, ide, monitor]
        # instead of [api, ide, monitor] so we have to manually replace them
        # see: https://github.com/AI4EOSC/ai4-papi/issues/22
        if label == 'deepaas':
            label = 'api'

        info['endpoints'][label] = f"http://{url}"

    # Add '/ui' to deepaas endpoint
    # If in the future we support other APIs, this will have to be removed.
    if 'api' in info['endpoints'].keys():
        info['endpoints']['api'] += '/ui'

    # Add quick-access (main endpoint) + customize endpoints
    service2endpoint = {
        'deepaas': 'api',
        'jupyter': 'ide',
        'vscode': 'ide',
    }
    try:  # deep-start compatible service
        service = re.search(
            'deep-start --(.*)$',
            info['docker_command'],
            ).group(1)

        info['main_endpoint'] = service2endpoint[service]

    except Exception:  # return first endpoint
        info['main_endpoint'] = list(info['endpoints'].keys())[0]

    # Only fill resources if the job is allocated
    allocs = Nomad.job.get_allocations(
        id_=j['ID'],
        namespace=namespace,
        )
    evals = Nomad.job.get_evaluations(
        id_=j['ID'],
        namespace=namespace,
        )
    if allocs:

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

        a = Nomad.allocation.get_allocation(allocs[idx]['ID'])

        # Add ID
        info['alloc_ID'] = a['ID']

        # Add datacenter
        info['datacenter'] = Nomad.node.get_node(a['NodeID'])['Datacenter']

        # Replace Nomad status with a more user-friendly status
        if a['ClientStatus'] == 'pending':
            info['status'] = 'starting'
        elif a['ClientStatus'] == 'unknown':
            info['status'] = 'down'
        else:
            # This status can be for example: "complete", "failed"
            info['status'] = a['ClientStatus']

        # Add error messages if needed
        if info['status'] == 'failed':
            info['error_msg'] = a['TaskStates']['main']['Events'][0]['Message']

            # Replace with clearer message
            if info['error_msg'] == 'Docker container exited with non-zero exit code: 1':
                info['error_msg'] = \
                    "An error seems to appear when running this Docker container. " \
                    "Try to run this Docker locally with the command " \
                    f"`{info['docker_command']}` to find what is the error " \
                    "or contact the module owner."

        elif  info['status'] == 'down':
            info['error_msg'] = \
                "There seems to be network issues in the cluster. Please wait until " \
                "the network is restored and you should be able to fully recover " \
                "your deployment."

        # Add resources
        res = a['AllocatedResources']['Tasks']['main']
        gpu = [d for d in res['Devices'] if d['Type'] == 'gpu'][0] if res['Devices'] else None
        cpu_cores = res['Cpu']['ReservedCores']
        info['resources'] = {
            'cpu_num': len(cpu_cores) if cpu_cores else 0,
            'cpu_MHz': res['Cpu']['CpuShares'],
            'gpu_num': len(gpu['DeviceIDs']) if gpu else 0,
            'memory_MB': res['Memory']['MemoryMB'],
            'disk_MB': a['AllocatedResources']['Shared']['DiskMB'],
        }

        # Retrieve the node the jobs landed at in order to properly fill the endpoints
        n = Nomad.node.get_node(a['NodeID'])
        for k, v in info['endpoints'].items():
            info['endpoints'][k] = v.replace('${meta.domain}', n['Meta']['domain'])

        # Add active endpoints
        if full_info:
            info['active_endpoints'] = []
            for k, v in info['endpoints'].items():
                try:
                    r = session.get(v, timeout=2)
                    if r.status_code == 200:
                        info['active_endpoints'].append(k)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    continue

        # Disable access to endpoints if there is a network cut
        if info['status'] == 'down' and info['active_endpoints']:
            info['active_endpoints'] = []

    elif evals:
        # Something happened, job didn't deploy (eg. job needs port that's currently being used)
        # We have to return `placement failures message`.
        info['status'] = 'error'
        info['error_msg'] = f"{evals[0].get('FailedTGAllocs', '')}"

    else:
        # info['error_msg'] = f"Job has not been yet evaluated. Contact with support sharing your job ID: {j['ID']}."
        info['status'] = 'queued'

        # Fill info with _requested_ resources instead
        res = usertask['Resources']
        gpu = [d for d in res['Devices'] if d['Name'] == 'gpu'][0] if res['Devices'] else None
        info['resources'] = {
            'cpu_num': res['Cores'],
            'cpu_MHz': 0,  # not known before allocation
            'gpu_num': gpu['Count'] if gpu else 0,
            'memory_MB': res['MemoryMB'],
            'disk_MB': j['TaskGroups'][0]['EphemeralDisk']['SizeMB'],
        }

    return info


def load_job_conf(
    raw_str: str,
    ):
    """
    Transform raw hcl string to Nomad dict object
    """
    return Nomad.jobs.parse(raw_str)


def create_deployment(
    conf: dict,
    ):
    """
    Submit a deployment to Nomad.
    """
    # Submit job
    try:
        _ = Nomad.jobs.register_job({'Job': conf})
        return {
            'status': 'success',
            'job_ID': conf['ID'],
        }
    except Exception as e:
        return {
            'status': 'fail',
            'error_msg': str(e),
        }


def delete_deployment(
    deployment_uuid: str,
    namespace: str,
    owner: str,
    ):
    """
    Delete a deployment. Users can only delete their own deployments.

    Parameters:
    * **vo**: Virtual Organization where your deployment is located
    * **deployment_uuid**: uuid of deployment to delete

    Returns a dict with status
    """
    # Retrieve the deployment information. Under-the-hood it checks that:
    # - the job indeed exists
    # - the owner does indeed own the job
    info = get_deployment(
        deployment_uuid=deployment_uuid,
        namespace=namespace,
        owner=owner,
        full_info=False,
        )

    # If job is in stuck status, allow deleting with purge.
    # Most of the time, when a job is in this status, it is due to a platform error.
    # It gets stuck and cannot be deleted without purge
    if info['status'] in ['queued', 'complete', 'failed', 'error', 'down'] :
        purge = True
    else:
        purge = False

    # Delete deployment
    Nomad.job.deregister_job(
        id_=deployment_uuid,
        namespace=namespace,
        purge=purge,
        )

    return {'status': 'success'}


@cached(cache=TTLCache(maxsize=1024, ttl=1*60*60))
def get_gpu_models(vo):
    """
    Retrieve available GPU models in the cluster, filtering nodes by VO.
    """
    gpu_models = set()
    nodes = Nomad.nodes.get_nodes(resources=True)
    for node in nodes:
        # Discard nodes that don't belong to the requested VO
        meta = Nomad.node.get_node(node['ID'])['Meta']
        if papiconf.MAIN_CONF['nomad']['namespaces'][vo] not in meta['namespace']:
            continue

        # Discard GPU models of nodes that are not eligible
        if node['SchedulingEligibility'] != 'eligible':
            continue

        # Retrieve GPU models of the node
        devices = node['NodeResources']['Devices']
        gpus = [d for d in devices if d['Type'] == 'gpu'] if devices else []
        for gpu in gpus:
            gpu_models.add(gpu['Name'])

    return list(gpu_models)
