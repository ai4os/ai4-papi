from copy import deepcopy
import types
from typing import Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, module_patches, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad


router = APIRouter(
    prefix="/modules",
    tags=["modules"],
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

    # Update common parts (modules and tools) of nomad job conf
    nomad_conf = nomad.fill_job_conf(
        job_conf=nomad_conf,
        user_conf=user_conf,
        namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
        owner=auth_info['id'],
        domain=papiconf.MAIN_CONF['lb']['domain'][vo],
    )

    # Update module-specific parts in nomad conf

    # Replace task conf in Nomad job
    task = nomad_conf['TaskGroups'][0]['Tasks'][0]

    # Set GPUs
    if user_conf['hardware']['gpu_num'] <= 0:
        del task['Resources']['Devices']
    else:
        task['Resources']['Devices'][0]['Count'] = user_conf['hardware']['gpu_num']
        if not user_conf['hardware']['gpu_type']:
            del task['Resources']['Devices'][0]['Affinities']
        else:
            task['Resources']['Devices'][0]['Affinities'][0]['RTarget'] = user_conf['hardware']['gpu_type']

    # Set Env variables
    task['Env']['RCLONE_CONFIG_RSHARE_URL'] = user_conf['storage']['rclone_url']
    task['Env']['RCLONE_CONFIG_RSHARE_VENDOR'] = user_conf['storage']['rclone_vendor']
    task['Env']['RCLONE_CONFIG_RSHARE_USER'] = user_conf['storage']['rclone_user']
    task['Env']['RCLONE_CONFIG_RSHARE_PASS'] = user_conf['storage']['rclone_password']
    task['Env']['RCLONE_CONFIG'] = user_conf['storage']['rclone_conf']

    # Apply patches if needed
    task = module_patches.patch_nextcloud_mount(
        user_conf['general']['docker_image'],
        task
    )

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
