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
import urllib
import uuid

from fastapi import HTTPException
import nomad
from nomad.api import exceptions

from ai4papi import utils
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


def get_deployments(
    namespace: str,
    owner: str,
    ):
    """
    Returns a list of all deployments belonging to a user, in a given namespace.
    """

    # user_jobs = []
    # for namespace in namespaces:

    # Filter jobs
    jobs = Nomad.jobs.get_jobs(namespace=namespace)  # job summaries
    fjobs = []

    for j in jobs:
        # Skip deleted jobs
        if j['Status'] == 'dead':
            continue

        # Skip jobs that do not start with userjob
        # (useful for admins who might have deployed other jobs eg. Traefik)
        if not j['Name'].startswith('userjob'):
            continue

        # Get full job description
        j = Nomad.job.get_job(
            id_=j['ID'],
            namespace=namespace,
            )

        # Remove jobs not belonging to owner
        if j['Meta'] and (owner == j['Meta'].get('owner', '')):
            fjobs.append(j)

    return fjobs


def get_deployment(
    deployment_uuid: str,
    namespace: str,
    owner: str,
    ):
    """
    Retrieve the info of a specific deployment.
    Format outputs to a Nomad-independent format to be used by the Dashboard

    Parameters:
    * **vo**: Virtual Organization from where you want to retrieve your deployment
    * **deployment_uuid**: uuid of deployment to gather info about

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
        'main_endpoint': None,
        'alloc_ID': None,
    }

    # Retrieve Docker image
    for t in j['TaskGroups'][0]['Tasks']:
        if t['Name'] == 'usertask':
            info['docker_image'] = t['Config']['image']
            command = t['Config'].get('command', '')
            args = t['Config'].get('args', [])
            info['docker_command'] = f"{command} {' '.join(args)}".strip()

    # Add endpoints
    info['endpoints'] = {}
    for s in j['TaskGroups'][0]['Services']:
        label = s['PortLabel']

        # Iterate through tags to find `Host` tag
        for t in s['Tags']:
            try:
                url = re.search('Host\(`(.+?)`', t).group(1)
                break
            except Exception:
                url = "missing-endpoint"

        # Old deployments had network ports with names [deepaas, ide, monitor]
        # instead of [api, ide, monitor] so we have to manually replace them
        # see: https://github.com/AI4EOSC/ai4-papi/issues/22
        if label == 'deepaas':
            label = 'api'

        info['endpoints'][label] = f"http://{url}"

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

        # Customize deepaas endpoint
        if service == 'deepaas':
            info['endpoints']['api'] += '/ui'

        info['main_endpoint'] = info['endpoints'][service2endpoint[service]]

    except Exception:  # return first endpoint
        info['main_endpoint'] = list(info['endpoints'].values())[0]

    # Only fill (resources + endpoints) if the job is allocated
    allocs = Nomad.job.get_allocations(
        id_=j['ID'],
        namespace=namespace,
        )
    evals = Nomad.job.get_evaluations(
        id_=j['ID'],
        namespace=namespace,
        )
    if allocs:

        # Keep only the most recent allocation per job
        dates = [a['CreateTime'] for a in allocs]
        idx = dates.index(max(dates))
        a = Nomad.allocation.get_allocation(allocs[idx]['ID'])

        # Add ID and status
        info['alloc_ID'] = a['ID']

        if a['ClientStatus'] == 'pending':
            info['status'] = 'starting'  # starting is clearer than pending, like done in the UI
        else:
            info['status'] = a['ClientStatus']

        if info['status'] == 'failed':
            info['error_msg'] = a['TaskStates']['usertask']['Events'][0]['Message']

            # Replace with clearer message
            if info['error_msg'] == 'Docker container exited with non-zero exit code: 1':
                info['error_msg'] = \
                    "An error seems to appear when running this Docker container. " \
                    "Try to run this Docker locally with the command " \
                    f"`{info['docker_command']}` to find what is the error " \
                    "or contact the module owner."

        # Add resources
        res = a['AllocatedResources']
        devices = res['Tasks']['usertask']['Devices']
        cpu_cores = res['Tasks']['usertask']['Cpu']['ReservedCores']
        info['resources'] = {
            'cpu_num': len(cpu_cores) if cpu_cores else 0,
            # 'cpu_MHz': res['Tasks']['usertask']['Cpu']['CpuShares'],  #TODO: uncomment when Dashboard is prepared
            'gpu_num': sum([1 for d in devices if d['Type'] == 'gpu']) if devices else 0,  #TODO: this misses multi-GPU deployments
            'memoryMB': res['Tasks']['usertask']['Memory']['MemoryMB'],
            'diskMB': res['Shared']['DiskMB'],
        }

    elif evals:
        # Something happened, job didn't deploy (eg. job needs port that's currently being used)
        # We have to return `placement failures message`.
        info['status'] = 'error'
        info['error_msg'] = f"{evals[0]['FailedTGAllocs']}"

    else:
        # info['error_msg'] = f"Job has not been yet evaluated. Contact with support sharing your job ID: {j['ID']}."
        info['status'] = 'queued'

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

    # Delete deployment
    Nomad.job.deregister_job(
        id_=deployment_uuid,
        namespace=namespace,
        purge=False,
        )

    return {'status': 'success'}
