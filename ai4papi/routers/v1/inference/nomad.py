from copy import deepcopy
import re
import types
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, module_patches, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad


router = APIRouter(
    prefix="/inferences",
    tags=["Inferences temporal deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


@router.get("/")
def get_inferences(
    vos: Union[Tuple, None] = Query(default=None),
    full_info: bool = Query(default=False),
    authorization=Depends(security),
    ):
    """
    Returns a list of all temporal deployments belonging to a user.

    Parameters:
    * **vo**: Virtual Organizations from where you want to retrieve your temporal deployments.
      If no vo is provided, it will retrieve the temporal deployments of all VOs.
    * **full_info**: retrieve the full information of each temporal deployment.
      Disabled by default, as it will increase latency too much if there are many
      temporal deployments.
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

    inferences = []
    for vo in vos:
        # Retrieve all jobs in namespace
        jobs = nomad.get_deployments(
            namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            owner=auth_info['id'],
            name='inference'
        )

        # Retrieve info for jobs in namespace
        for j in jobs:
            try:
                job_info = get_inference(
                    vo=vo,
                    deployment_uuid=j['ID'],
                    full_info=full_info,
                    authorization=types.SimpleNamespace(
                        credentials=authorization.credentials  # token
                    ),
                )
            except HTTPException:  # not a module
                continue
            except Exception as e:  # unexpected error
                raise(e)

            inferences.append(job_info)

    # Sort temporal deployments by creation date
    seq = [j['submit_time'] for j in inferences]
    args = sorted(range(len(seq)), key=seq.__getitem__)[::-1]
    sorted_jobs = [inferences[i] for i in args]

    return sorted_jobs


@router.get("/{deployment_uuid}")
def get_inference(
    vo: str,
    deployment_uuid: str,
    full_info: bool = Query(default=True),
    authorization=Depends(security),
    ):
    """
    Retrieve the info of a specific temporal deployment.
    Format outputs to a Nomad-independent format to be used by the Dashboard

    Parameters:
    * **vo**: Virtual Organization from where you want to retrieve your temporal deployment
    * **deployment_uuid**: uuid of temporal deployment to gather info about
    * **full_info**: retrieve the full information of that temporal deployment (may increase
      latency)

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
        full_info=full_info,
    )

    # Check the temporal deployment is indeed a module
    tool_list = papiconf.TOOLS.keys()
    module_name = re.search(
            '/(.*):',  # remove dockerhub account and tag
            job['docker_image'],
            ).group(1)
    if module_name in tool_list:
        raise HTTPException(
            status_code=400,
            detail="This deployment is a tool, not a module.",
            )

    return job


@router.post("/")
def create_inference(
    vo: str,
    authorization=Depends(security),
    ):
    """
    Submit a temporal deployment to Nomad.

    Parameters:
    * **vo**: Virtual Organization where you want to create your temporal deployment
    
    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Load inference configuration
    nomad_conf = deepcopy(papiconf.INFERENCES['nomad'])
    user_conf = deepcopy(papiconf.INFERENCES['user']['values'])

    # Check if the provided configuration is within the job quotas
    quotas.check_jobwise(
        conf=user_conf,
        vo=vo,
    )

    # Check if the provided configuration is within the user quotas
    deployments = get_inferences(
        vos=[vo],
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials  # token
        ),
    )
    quotas.check_userwise(
        conf=user_conf,
        deployments=deployments,
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
    utils.check_domain(domain)

    #TODO: remove when we solve disk issues
    # For now on we fix disk here because, if not fixed, jobs are not being deployed
    # (ie. "resource disk exhausted").
    # In any case, this limit is useless because it has not yet been passed to docker
    user_conf['hardware']['disk'] = 500

    # Replace the Nomad job template
    nomad_conf = nomad_conf.safe_substitute(
        {
            'JOB_UUID': job_uuid,
            'NAMESPACE': papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            'PRIORITY': priority,
            'OWNER': auth_info['id'],
            'OWNER_NAME': auth_info['name'],
            'OWNER_EMAIL': auth_info['email'],
            'TITLE': user_conf['general']['title'][:45],  # keep only 45 first characters
            'DESCRIPTION': user_conf['general']['desc'][:1000],  # limit to 1K characters
            'DOMAIN': domain,
            'DOCKER_IMAGE': user_conf['general']['docker_image'],
            'DOCKER_TAG': user_conf['general']['docker_tag'],
            'SERVICE': user_conf['general']['service'],
            'CPU_NUM': user_conf['hardware']['cpu_num'],
            'RAM': user_conf['hardware']['ram'],
            'DISK': user_conf['hardware']['disk'],
            'SHARED_MEMORY': user_conf['hardware']['ram'] * 10**6 * 0.5,
            # Limit at 50% of RAM memory, in bytes
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

    # Modify the GPU section
    if user_conf['hardware']['gpu_num'] <= 0:
        # Delete GPU section in CPU deployments
        usertask['Resources']['Devices'] = None
    else:
        # If gpu_type not provided, remove constraint to GPU model
        if not user_conf['hardware']['gpu_type']:
            usertask['Resources']['Devices'][0]['Constraints'] = None

    # If storage credentials not provided, remove storage-related tasks
    if not all(user_conf['storage'].values()):
        tasks[:] = [t for t in tasks if t['Name'] not in {'storagetask', 'storagecleanup'}]

    # Submit job
    r = nomad.create_deployment(nomad_conf)

    return r


@router.delete("/{deployment_uuid}")
def delete_inference(
    vo: str,
    deployment_uuid: str,
    authorization=Depends(security),
    ):
    """
    Delete a temporal deployment. Users can only delete their own deployments.

    Parameters:
    * **vo**: Virtual Organization where your temporal deployment is located
    * **deployment_uuid**: uuid of temporal deployment to delete

    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Delete temporal deployment
    r = nomad.delete_deployment(
        deployment_uuid=deployment_uuid,
        namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
        owner=auth_info['id'],
    )

    return r
