from copy import deepcopy
import types
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, module_patches, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad


router = APIRouter(
    prefix="/modules",
    tags=["Modules deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


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
    auth_info = auth.get_user_info(token=authorization.credentials)

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
        # Retrieve all jobs in namespace
        jobs = nomad.get_deployments(
            namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            owner=auth_info['id'],
        )

        # Retrieve info for jobs in namespace
        for j in jobs:
            try:
                job_info = get_deployment(
                    vo=vo,
                    deployment_uuid=j['ID'],
                    authorization=types.SimpleNamespace(
                        credentials=authorization.credentials  # token
                    ),
                )
            except Exception:  # not a module
                continue
            user_jobs.append(job_info)

    # Sort deployments by creation date
    seq = [j['submit_time'] for j in user_jobs]
    args = sorted(range(len(seq)), key=seq.__getitem__)[::-1]
    sorted_jobs = [user_jobs[i] for i in args]

    return sorted_jobs


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
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF['nomad']['namespaces'][vo]

    job = nomad.get_deployment(
        deployment_uuid=deployment_uuid,
        namespace=namespace,
        owner=auth_info['id'],
    )

    # Check the deployment is indeed a module
    tool_list = papiconf.TOOLS.keys()
    module_name = job['docker_image'].split('/')[1]  # deephdc/*
    if  module_name in tool_list:
        raise HTTPException(
            status_code=400,
            detail="This deployment is a tool, not a module.",
            )

    # Customize deepaas endpoint
    job['endpoints']['deepaas'] += '/ui'

    return job


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
            "docker_image": "deephdc/deep-oc-image-classification-tf",
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
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Load module configuration
    nomad_conf = deepcopy(papiconf.MODULES['nomad'])
    user_conf = deepcopy(papiconf.MODULES['user']['values'])

    # Update values conf in case we received a submitted conf
    if conf is not None:
        user_conf = utils.update_values_conf(
            submitted=conf,
            reference=user_conf,
        )

    # Check if the provided configuration is within the quotas
    quotas.check(
        conf=user_conf,
        vo=vo,
    )

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Jobs from tutorial users should have low priority (ie. can be displaced if needed)
    if vo == 'training.egi.eu':
        priority = 25
    else:
        priority = 50

    # Generate a domain for user-app and check nothing is running there
    domain = utils.generate_domain(
        hostname=user_conf['general']['hostname'],
        base_domain=papiconf.MAIN_CONF['lb']['domain'][vo],
        job_uuid=job_uuid,
    )
    utils.check_domain(f"http://{domain}")

    # Replace the Nomad job template
    nomad_conf = nomad_conf.safe_substitute(
        {
            'JOB_UUID': job_uuid,
            'NAMESPACE': papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            'PRIORITY': priority,
            'OWNER': auth_info['id'],
            'TITLE': user_conf['general']['title'][:45],  # keep only 45 first characters
            'DESCRIPTION': user_conf['general']['desc'][:1000],  # limit to 1K characters
            'DOMAIN': domain,
            'DOCKER_IMAGE': user_conf['general']['docker_image'],
            'DOCKER_TAG': user_conf['general']['docker_tag'],
            'SERVICE': user_conf['general']['service'],
            'CPU_NUM': user_conf['hardware']['cpu_num'],
            'RAM': user_conf['hardware']['ram'],
            'DISK': user_conf['hardware']['disk'],
            'GPU_NUM': user_conf['hardware']['gpu_num'],
            'GPU_MODELNAME': user_conf['hardware']['gpu_type'],
            'JUPYTER_PASSWORD': user_conf['general']['jupyter_password'],
            'RCLONE_CONFIG_RSHARE_URL': user_conf['storage']['rclone_url'],
            'RCLONE_CONFIG_RSHARE_VENDOR': user_conf['storage']['rclone_vendor'],
            'RCLONE_CONFIG_RSHARE_USER': user_conf['storage']['rclone_user'],
            'RCLONE_CONFIG_RSHARE_PASS': user_conf['storage']['rclone_password'],
            'RCLONE_CONFIG': user_conf['storage']['rclone_conf'],
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    tasks = nomad_conf['TaskGroups'][0]['Tasks']
    usertask = [t for t in tasks if t['Name']=='usertask'][0]

    # Apply patches if needed
    usertask = module_patches.patch_nextcloud_mount(
        user_conf['general']['docker_image'],
        usertask
    )

    # Delete GPU section if not needed
    if user_conf['hardware']['gpu_num'] <= 0:
        del usertask['Resources']['Devices']

    # Submit job
    r = nomad.create_deployment(nomad_conf)

    return r


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
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Delete deployment
    r = nomad.delete_deployment(
        deployment_uuid=deployment_uuid,
        namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
        owner=auth_info['id'],
    )

    return r
