"""
Manage deployments with Nomad (Training API).

todo: 
* Once authentication is implemented, the `owner` param in the functions can possibly
 be removed and derived from the token
* should '/deployments' be renamed to '/trainings'?
"""
from copy import deepcopy
from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException
import nomad

from ai4eosc.conf import NOMAD_JOB_CONF, USER_CONF_VALUES
# from ai4eosc.dependencies import get_token_header


router = APIRouter(
    prefix="/deployments",
    tags=["deployments"],
    # dependencies=[Depends(get_token_header)],
    responses={404: {"description": "Not found"}},
)

Nomad = nomad.Nomad()


@router.get("/")
def get_deployments(
        owner: str = None,
):
    """
    Retrieve all deployments belonging to a user.
    If no username is provided return all deployments.

    Parameters:
    * **owner**: string with username (will be removed once we implement token authentication)
    
    Returns a list of deployments.
    """
    jobs = Nomad.jobs.get_jobs()  # job summaries

    # Filter jobs
    njobs = []
    for j in jobs:
        # Skip deleted jobs
        if j['Status'] == 'dead':
            continue

        # Get full job description instead
        j = Nomad.job.get_job(j['ID'])

        # Remove jobs not belonging to owner
        if j['Meta'] and (owner == j['Meta'].get('owner', '')):
            njobs.append(j)

    # Format to a Nomad-independent format to be used by the Dashboard
    fjobs = []
    for j in njobs:
        tmp = {
            'job_ID': j['ID'],
            'status': j['Status'],
            'owner': j['Meta']['owner'],
            'submit_time': datetime.fromtimestamp(
                j['SubmitTime'] // 1000000000
            ).strftime('%Y-%m-%d %H:%M:%S'),  # nanoseconds to timestamp
        }

        allocs = Nomad.job.get_allocations(j['ID'])
        if allocs:
            a = Nomad.allocation.get_allocation(allocs[0]['ID'])  # only keep the first allocation per job

            tmp['alloc_ID'] = a['ID']

            res = a['AllocatedResources']
            devices = res['Tasks']['usertask']['Devices']
            tmp['resources'] = {
                'cpu_num': res['Tasks']['usertask']['Cpu']['CpuShares'],
                'gpu_num': sum([1 for d in devices if d['Type'] == 'gpu']) if devices else 0,
                'memoryMB': res['Tasks']['usertask']['Memory']['MemoryMB'],
                'diskMB': res['Shared']['DiskMB'],
            }

            public_ip = 'https://xxx.xxx.xxx.xxx'  # todo: replace when ready
            ports = a['Resources']['Networks'][0]['DynamicPorts']
            tmp['endpoints'] = {d['Label']: f"{public_ip}:{d['Value']}" for d in ports}
            # todo: We need to connect internal IP (172.XXX) to external IP (193.XXX) (Traefik + Consul Connect)
            # todo: use service discovery to map internal ip to external IPs???

        else:
            # Something happened, job didn't deploy (eg. jobs needs port that's currently being used)
            # We have to return `placement failures message`.
            evals = Nomad.job.get_evaluations(j['ID'])
            tmp['error_msg'] = f"{evals[0]['FailedTGAllocs']}"
            # todo: improve this error message once we start seeing the different modes of failures in typical cases

        fjobs.append(tmp)

    return fjobs


@router.get("/{deployment_uuid}")
def get_deployment(
        deployment_uuid: str,
        owner: str = None,
):
    """
    Retrieve the info of a specific deployment.
    """
    raise HTTPException(status_code=501)  # Not implemented #todo: implement if finally needed


@router.post("/")
def create_deployment(
        owner: str = None,
        conf: dict = {},
):
    """
    Submit a deployment to Nomad.

    Parameters:
    * **owner**: string with username (will be removed once we implement token authentication)
    * **conf**: configuration dict of the deployment to be submitted. 
    If only a partial configuration is submitted, the remaining will be
    filled with default args (see GET(`/info/conf`) method).
    
    Returns a dict with status
    """
    # Enforce job owner
    if not owner:
        raise ValueError("You must provide a owner of the deployment. For testing purposes you can use 'janedoe'.")

    # Update default dict with new values
    job_conf, user_conf = deepcopy(NOMAD_JOB_CONF), deepcopy(USER_CONF_VALUES)
    user_conf.update(conf)

    # Enforce JupyterLab password minimum number of characters
    if (
        user_conf['general']['service'] == 'jupyterlab' and
        len(user_conf['general']['jupyter_password']) < 9
        ):
        raise HTTPException(
            status_code=501,
            detail="JupyterLab password should have at least 9 characters."
            )

    # Assign unique job ID (if submitting job with existing ID, the existing job gets replaced)
    job_conf['ID'] = 'example2'  # todo: remove when ready
    # job_conf['ID'] = uuid.uuid1()  # id is generated from (MAC address+timestamp) so it's unique

    job_conf['Meta']['owner'] = owner  # todo: is there a more appropriate field than `meta` for this?
    job_conf['Meta']['title'] = user_conf['general']['title'][:45]  # keep only 45 first characters
    job_conf['Meta']['description'] = user_conf['general']['desc']

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
        deployment_uuid: str,
        owner: str = None,
):
    """
    Delete a deployment. Users can only delete their own deployments.

    Parameters:
    * **owner**: string with username (will be removed once we implement token authentication)
    * **deployment_uuid**: uuid of deployment to delete

    Returns a dict with status
    """

    # Enforce job owner
    if not owner:
        raise ValueError("You must provide a owner of the deployment. For testing purposes you can use 'janedoe'.")

    # Check the deployment exists and belongs to the user
    deployments = get_deployments(owner=owner)
    if deployment_uuid not in {d['ID'] for d in deployments}:
        return {
            'status': 'fail',
            'error_msg': 'Deployment does not exist, or does not belong to the provided owner.',
        }

    # Delete deployment
    Nomad.job.deregister_job(deployment_uuid)

    return {'status': 'success'}
