"""
Manage deployments with Nomad.
This is the AI4EOSC Training API.

Notes:
=====
* Terminology warning: what we call a "deployment" (as in `create_deployment`)
 is a Nomad "job" (not a Nomad "deployment"!)
"""

from copy import deepcopy
from datetime import datetime
import re
import types
from typing import Tuple, Union
import urllib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
import nomad
from nomad.api import exceptions

from ai4papi import quotas, utils
from ai4papi.auth import get_user_info
from ai4papi.conf import NOMAD_JOB_CONF, USER_CONF_VALUES, MAIN_CONF


router = APIRouter(
    prefix="/deployments",
    tags=["deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

Nomad = nomad.Nomad()
# TODO: Remove monkey-patches when the code is merged to python-nomad Pypi package
Nomad.job.deregister_job = types.MethodType(
    utils.deregister_job,
    Nomad.job
    )
Nomad.job.get_allocations = types.MethodType(
    utils.get_allocations,
    Nomad.job
    )
Nomad.job.get_evaluations = types.MethodType(
    utils.get_allocations,
    Nomad.job
    )

@router.get("/")
def get_deployments(
    vos: Union[Tuple, None] = Query(default=None),
    authorization=Depends(security),
    ):
    """
    Returns a list of all deployments belonging to a user.

    Parameters:
    * **vo**: Virtual Organizations from where you want to retrieve your deployments.
      If no vo is provided, it will retrieve the deployments of all VOs.
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)

    # If no VOs, then retrieve jobs from all user VOs
    # Else only retrieve from allowed VOs
    if not vos:
        vos = auth_info['vos']
    else:
        vos = set(vos).intersection(auth_info['vos'])

    if not vos:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organizations do not match with any of your available VOs: {auth_info['vos']}."
            )

    user_jobs = []
    for vo in vos:

        # Retrieve the associated namespace to that VO
        namespace = MAIN_CONF['nomad']['namespaces'][vo]

        # Filter jobs
        jobs = Nomad.jobs.get_jobs(namespace=namespace)  # job summaries
        njobs = []

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
                id=j['ID'],
                namespace=namespace,
                )

            # Remove jobs not belonging to owner
            if j['Meta'] and (auth_info['id'] == j['Meta'].get('owner', '')):
                njobs.append(j)

        # Retrieve info for jobs
        fjobs = []
        for j in njobs:

            job_info = get_deployment(
                vo=vo,
                deployment_uuid=j['ID'],
                authorization=types.SimpleNamespace(
                    credentials=authorization.credentials  # token
                ),
            )

            fjobs.append(job_info)

        user_jobs += fjobs

    return user_jobs


@router.get("/{deployment_uuid}")
def get_deployment(
    vo: str,
    deployment_uuid: str,
    authorization=Depends(security),
    ):
    """
    Retrieve the info of a specific deployment.
    Format outputs to a Nomad-independent format to be used by the Dashboard

    Parameters:
    * **vo**: Virtual Organization from where you want to retrieve your deployment
    * **deployment_uuid**: uuid of deployment to gather info about

    Returns a dict with info
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)

    # Check VO permissions
    if vo not in auth_info['vos']:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organization does not match with any of your available VOs: {auth_info['vos']}."
            )

    # Retrieve the associated namespace to that VO
    namespace = MAIN_CONF['nomad']['namespaces'][vo]

    # Check the deployment exists
    try:
        j = Nomad.job.get_job(
            id=deployment_uuid,
            namespace=namespace,
            )
    except exceptions.URLNotFoundNomadException:
        raise HTTPException(
            status_code=400,
            detail="No deployment exists with this uuid.",
            )

    # Check job does belong to owner
    if j['Meta'] and auth_info['id'] != j['Meta'].get('owner', ''):
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
        'submit_time': datetime.fromtimestamp(
            j['SubmitTime'] // 1000000000
        ).strftime('%Y-%m-%d %H:%M:%S'),  # nanoseconds to timestamp
        'resources': {},
        'endpoints': {},
        'alloc_ID': None,
    }

    # Retrieve Docker image
    for t in j['TaskGroups'][0]['Tasks']:
        if t['Name'] == 'usertask':
            info['docker_image'] = t['Config']['image']

    # Add endpoints
    info['endpoints'] = {}
    for s in j['TaskGroups'][0]['Services']:
        label = s['PortLabel']
        try:
            url = re.search('Host\(`(.+?)`', s['Tags'][1]).group(1)
        except Exception:
            url = "missing-endpoint"
        info['endpoints'][label] = f"http://{url}"

    # Only fill (resources + endpoints) if the job is allocated
    allocs = Nomad.job.get_allocations(j['ID'], namespace=namespace)
    evals = Nomad.job.get_evaluations(j['ID'], namespace=namespace)
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

        # Add resources
        res = a['AllocatedResources']
        devices = res['Tasks']['usertask']['Devices']
        info['resources'] = {
            'cpu_num': res['Tasks']['usertask']['Cpu']['CpuShares'],
            'gpu_num': sum([1 for d in devices if d['Type'] == 'gpu']) if devices else 0,
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


@router.post("/")
def create_deployment(
    vo: str,
    conf: Union[dict, None] = None,
    authorization=Depends(security),
    ):
    """
    Submit a deployment to Nomad.

    Parameters:
    * **vo**: Virtual Organization where you want to create your deployment
    * **conf**: configuration dict of the deployment to be submitted.
    For example:
    ```
    {
        "general":{
            "docker_image": "deephdc/deep-oc-image-classification-tf:cpu",
            "service": "deepaas"
        },
        "hardware": {
            "cpu_num": 4
        }
    }
    ```
    If only a partial configuration is submitted, the remaining will be filled with
    [default args](https://github.com/AI4EOSC/ai4-papi/blob/master/etc/userconf.yaml)

    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)

    # Check VO permissions
    if vo not in auth_info['vos']:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organization does not match with any of your available VOs: {auth_info['vos']}."
            )

    # Update default dict with new values
    job_conf, user_conf = deepcopy(NOMAD_JOB_CONF), deepcopy(USER_CONF_VALUES)
    if conf is not None:
        for k in conf.keys():

            # Check level 1 keys
            if k not in user_conf.keys():
                raise HTTPException(
                    status_code=400,
                    detail=f"The key `{k}` in not a valid parameter."
                    )

            # Check level 2 keys
            s1 = set(conf[k].keys())
            s2 = set(user_conf[k].keys())
            subs = s1.difference(s2)
            if subs:
                raise HTTPException(
                    status_code=400,
                    detail=f"The keys `{subs}` are not a valid parameters."
                    )

            user_conf[k].update(conf[k])

    # Enforce JupyterLab password minimum number of characters
    if (
        user_conf['general']['service'] in ['jupyter', 'vscode'] and
        len(user_conf['general']['jupyter_password']) < 9
        ):
        raise HTTPException(
            status_code=400,
            detail="JupyterLab password should have at least 9 characters."
            )

    # Check the provided configuring is with the quotas
    quotas.check(user_conf)

    # Assign unique job ID (if submitting job with existing ID, the existing job gets replaced)
    job_uuid = uuid.uuid1()  # generated from (MAC address+timestamp) so it's unique
    job_conf['ID'] = f"{job_uuid}"
    job_conf['Name'] = f'userjob-{job_uuid}'

    # Retrieve the associated namespace to that VO
    job_conf['namespace'] = MAIN_CONF['nomad']['namespaces'][vo]

    # Add owner and extra information to the job metadata
    job_conf['Meta']['owner'] = auth_info['id']
    job_conf['Meta']['title'] = user_conf['general']['title'][:45]  # keep only 45 first characters
    job_conf['Meta']['description'] = user_conf['general']['desc'][:1000]  # limit to 1K characters

    # Create the Traefik endpoints where the deployment is going to be accessible
    hname = user_conf['general']['hostname']
    domain = MAIN_CONF['lb']['domain'][vo]
    if hname:
        if '.' in hname:  # user provide full domain
            if not hname.startswith('http'):
                hname = f'http://{hname}'
            base_domain = urllib.parse.urlparse(hname).hostname
        else:  # user provides custom subdomain
            if hname in ['www']:
                raise HTTPException(
                    status_code=400,
                    detail="Forbidden hostname: {hname}."
                    )
            base_domain = f"{hname}.{domain}"
    else:  # we use job_id as default subdomain
        base_domain = f"{job_conf['ID']}.{domain}"

    utils.check_domain(f"http://{base_domain}")  # check nothing is running there

    for service in job_conf['TaskGroups'][0]['Services']:
        sname = service['PortLabel']  # either ['deepaas', 'monitor', 'ide']
        service['Name'] = f"{job_conf['Name']}-{sname}"
        domain = f"{sname}.{base_domain}"
        service['Tags'].append(
            f"traefik.http.routers.{service['Name']}.rule=Host(`{domain}`, `www.{domain}`)"
        )

    # Replace user conf in Nomad job
    task = job_conf['TaskGroups'][0]['Tasks'][0]

    task['Config']['image'] = f"{user_conf['general']['docker_image']}:{user_conf['general']['docker_tag']}"
    task['Config']['command'] = "deep-start"
    task['Config']['args'] = [f"--{user_conf['general']['service']}"]

    # TODO: add `docker_privileged` arg if we still need it

    task['Resources']['CPU'] = user_conf['hardware']['cpu_num']
    task['Resources']['MemoryMB'] = user_conf['hardware']['ram']
    task['Resources']['DiskMB'] = user_conf['hardware']['disk']
    if user_conf['hardware']['gpu_num'] <= 0:
        del task['Resources']['Devices']
    else:

        # TODO: remove when Traefik issue if fixed (see job.nomad)
        raise HTTPException(
            status_code=500,
            detail="GPU deployments are temporarily disabled.",
            )

        task['Resources']['Devices'][0]['Count'] = user_conf['hardware']['gpu_num']
        if not user_conf['hardware']['gpu_type']:
            del task['Resources']['Devices'][0]['Affinities']
        else:
            task['Resources']['Devices'][0]['Affinities'][0]['RTarget'] = user_conf['hardware']['gpu_type']

    task['Env']['RCLONE_CONFIG_RSHARE_URL'] = user_conf['storage']['rclone_url']
    task['Env']['RCLONE_CONFIG_RSHARE_VENDOR'] = user_conf['storage']['rclone_vendor']
    task['Env']['RCLONE_CONFIG_RSHARE_USER'] = user_conf['storage']['rclone_user']
    task['Env']['RCLONE_CONFIG_RSHARE_PASS'] = user_conf['storage']['rclone_password']
    task['Env']['RCLONE_CONFIG'] = user_conf['storage']['rclone_conf']
    task['Env']['jupyterPASSWORD'] = user_conf['general']['jupyter_password']

    # Submit job
    try:
        response = Nomad.jobs.register_job({'Job': job_conf})
        return {
            'status': 'success',
            'job_id': job_conf['ID'],
        }
    except Exception as e:
        return {
            'status': 'fail',
            'error_msg': str(e),
        }


@router.delete("/{deployment_uuid}")
def delete_deployment(
    vo: str,
    deployment_uuid: str,
    authorization=Depends(security),
    ):
    """
    Delete a deployment. Users can only delete their own deployments.

    Parameters:
    * **vo**: Virtual Organization where your deployment is located
    * **deployment_uuid**: uuid of deployment to delete

    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)

    # Check VO permissions
    if vo not in auth_info['vos']:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organization does not match with any of your available VOs: {auth_info['vos']}."
            )

    # Retrieve the associated namespace to that VO
    namespace = MAIN_CONF['nomad']['namespaces'][vo]

    # Check the deployment exists
    try:
        j = Nomad.job.get_job(
            id=deployment_uuid,
            namespace=namespace,
            )
    except exceptions.URLNotFoundNomadException:
        raise HTTPException(
            status_code=400,
            detail="No deployment exists with this uuid.",
            )

    # Check job does belong to owner
    if j['Meta'] and auth_info['id'] != j['Meta'].get('owner', ''):
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
