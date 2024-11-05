from copy import deepcopy
import re
import secrets
import types
from types import SimpleNamespace
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad
from ai4papi.routers.v1 import secrets as ai4secrets


router = APIRouter(
    prefix="/tools",
    tags=["Tools deployments"],
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
            prefix='tool',
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
            except HTTPException:  # not a tool
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

    # Check the deployment is indeed a tool
    if not job['name'].startswith('tool'):
        raise HTTPException(
            status_code=400,
            detail="This deployment is not a tool.",
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

    # Retrieve toolname from configuration, else deploy first tool in the list
    try:
        tool_name = conf["general"]["docker_image"].split('/')[1]  # deephdc/*
    except Exception:
        tool_name = list(papiconf.TOOLS.keys())[0]

    # Load tool configuration
    nomad_conf = deepcopy(papiconf.TOOLS[tool_name]['nomad'])
    user_conf = deepcopy(papiconf.TOOLS[tool_name]['user']['values'])

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

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Jobs from tutorial users should have low priority (ie. can be displaced if needed)
    if vo == 'training.egi.eu':
        priority = 25
    else:
        priority = 50

    base_domain = papiconf.MAIN_CONF['lb']['domain'][vo]
    
    # Create a default secret for the Federated Server
    _ = ai4secrets.create_secret(
        vo=vo,
        secret_path=f"deployments/{job_uuid}/federated/default",
        secret_data={'token': secrets.token_hex()},
        authorization=SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )

    # Create a Vault token so that the deployment can access the Federated secret
    vault_token = ai4secrets.create_vault_token(
        jwt=authorization.credentials,
        issuer=auth_info['issuer'],
        ttl='365d',  # 1 year expiration date
    )

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
            'HOSTNAME': job_uuid,
            'DOCKER_IMAGE': user_conf['general']['docker_image'],
            'DOCKER_TAG': user_conf['general']['docker_tag'],
            'CPU_NUM': user_conf['hardware']['cpu_num'],
            'RAM': user_conf['hardware']['ram'],
            'DISK': user_conf['hardware']['disk'],
            'SHARED_MEMORY': user_conf['hardware']['ram'] * 10**6 * 0.5,
            # Limit at 50% of RAM memory, in bytes
            'JUPYTER_PASSWORD': user_conf['general']['jupyter_password'],
            'VAULT_TOKEN': vault_token,
            'FEDERATED_ROUNDS': user_conf['configuration']['rounds'],
            'FEDERATED_METRIC': user_conf['configuration']['metric'],
            'FEDERATED_MIN_FIT_CLIENTS': user_conf['configuration']['min_fit_clients'],
            'FEDERATED_MIN_AVAILABLE_CLIENTS': user_conf['configuration']['min_available_clients'],
            'FEDERATED_STRATEGY': user_conf['configuration']['strategy'],
            'MU_FEDPROX': user_conf['configuration']['mu'],
            'FEDAVGM_SERVER_FL' : user_conf['configuration']['fl'],
            'FEDAVGM_SERVER_MOMENTUM': user_conf['configuration']['momentum'],
            'DP': user_conf['configuration']['dp'],
            'NOISE_MULT': user_conf['configuration']['noise_mult'],
            'SAMPLED_CLIENTS': user_conf['configuration']['sampled_clients'],
            'CLIP_NORM': user_conf['configuration']['clip_norm']
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    tasks = nomad_conf['TaskGroups'][0]['Tasks']
    usertask = [t for t in tasks if t['Name']=='main'][0]

    # Launch `deep-start` compatible service if needed
    service = user_conf['general']['service']
    if service in ['deepaas', 'jupyter', 'vscode']:
        usertask['Config']['command'] = 'deep-start'
        usertask['Config']['args'] = [f'--{service}']

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

    # Remove Vault secrets belonging to that deployment
    r = ai4secrets.get_secrets(
        vo=vo,
        subpath=f"/deployments/{deployment_uuid}",
        authorization=SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )
    for path in r.keys():
        r = ai4secrets.delete_secret(
            vo=vo,
            secret_path=path,
            authorization=SimpleNamespace(
                credentials=authorization.credentials,
            ),
        )

    return r
