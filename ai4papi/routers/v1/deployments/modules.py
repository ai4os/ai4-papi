from copy import deepcopy
import datetime
import os
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
    prefix="/modules",
    tags=["Modules deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


@router.get("")
def get_deployments(
    vos: Union[Tuple, None] = Query(default=None),
    full_info: bool = Query(default=False),
    authorization=Depends(security),
    ):
    """
    Returns a list of all deployments belonging to a user.

    Parameters:
    * **vo**: Virtual Organizations from where you want to retrieve your deployments.
      If no vo is provided, it will retrieve the deployments of all VOs.
    * **full_info**: retrieve the full information of each deployment.
      Disabled by default, as it will increase latency too much if there are many
      deployments.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

    # If no VOs, then retrieve jobs from all user VOs
    # Always remove VOs that do not belong to the project
    if not vos:
        vos = auth_info['vos']
    vos = set(vos).intersection(set(papiconf.MAIN_CONF['auth']['VO']))
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
            prefix='module',
        )

        # Retrieve info for jobs in namespace
        for j in jobs:
            try:
                job_info = get_deployment(
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
    full_info: bool = Query(default=True),
    authorization=Depends(security),
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

    # Check the deployment is indeed a module
    if not job['name'].startswith('module'):
        raise HTTPException(
            status_code=400,
            detail="This deployment is not a module.",
            )

    return job


@router.post("")
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

    # Utils validate conf
    user_conf = utils.validate_conf(user_conf)

    # Check if the provided configuration is within the job quotas
    quotas.check_jobwise(
        conf=user_conf,
        vo=vo,
    )

    # Check if the provided configuration is within the user quotas
    deployments = get_deployments(
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

    # Remove non-compliant characters from hostname
    base_domain = papiconf.MAIN_CONF['lb']['domain'][vo]
    hostname = utils.safe_hostname(
        hostname=user_conf['general']['hostname'],
        job_uuid=job_uuid,
    )

    #TODO: reenable custom hostname, when we are able to parse all node metadata
    # (domain key) to build the true domain
    hostname = job_uuid

    # # Check the hostname is available in all data-centers
    # # (we don't know beforehand where the job will land)
    # #TODO: make sure this does not break if the datacenter is unavailable
    # #TODO: disallow custom hostname, pain in the ass, slower deploys
    # for datacenter in papiconf.MAIN_CONF['nomad']['datacenters']:
    #     utils.check_domain(f"{hostname}.{datacenter}-{base_domain}")

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
            'BASE_DOMAIN': base_domain,
            'HOSTNAME': hostname,
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
            'MAILING_TOKEN': os.getenv("MAILING_TOKEN", default=""),
            'PROJECT_NAME': papiconf.MAIN_CONF['nomad']['namespaces'][vo].upper(),
            'TODAY': str(datetime.date.today()),
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    tasks = nomad_conf['TaskGroups'][0]['Tasks']
    usertask = [t for t in tasks if t['Name']=='main'][0]

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

    # If storage credentials not provided, remove all storage-related tasks
    rclone = {k: v for k, v in user_conf['storage'].items() if k.startswith('rclone')}
    if not all(rclone.values()):
        exclude_tasks = ['storagetask', 'storagecleanup', 'dataset_download']
    else:
        # If datasets provided, replicate 'dataset_download' task as many times as needed
        if user_conf['storage']['datasets']:
            download_task = [t for t in tasks if t['Name'] == 'dataset_download'][0]
            for i, dataset in enumerate(user_conf['storage']['datasets']):
                t = deepcopy(download_task)
                t['Env']['DOI'] = dataset['doi']
                t['Env']['FORCE_PULL'] = dataset['doi']
                t['Name'] = f'dataset_download_{i}'
                tasks.append(t)
        # Always exclude initial 'dataset_download' task, as it is used as template
        exclude_tasks = ['dataset_download']

    tasks[:] = [t for t in tasks if t['Name'] not in exclude_tasks]

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
