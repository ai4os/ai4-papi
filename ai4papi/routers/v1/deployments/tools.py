from copy import deepcopy
import re
import types
from typing import Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad


router = APIRouter(
    prefix="/tools",
    tags=["Tools deployments"],
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
            except Exception:  # not a tool
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

    # Check the deployment is indeed a tool
    tool_list = papiconf.TOOLS.keys()
    tool_name = re.search(
            '/(.*):',  # remove dockerhub account and tag
            job['docker_image'],
            ).group(1)
    if tool_name not in tool_list:
        raise HTTPException(
            status_code=400,
            detail="This deployment is a module, not a tool.",
            )

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

    # Update tool-specific parts in nomad conf
    if tool_name == 'deep-oc-federated-server':

        # Add grpc support for federated learning
        for service in nomad_conf['TaskGroups'][0]['Services']:
            sname = service['PortLabel']
            if sname == 'fedserver':
                service['Name'] = f"{nomad_conf['Name']}-{sname}"
                service['Tags'].append(
                    f"traefik.http.services.{service['Name']}.loadbalancer.server.scheme=h2c"
                )
                # TODO: add Federated server authentication (via Traefik Basic Auth)

        # Replace task conf in Nomad job
        task = nomad_conf['TaskGroups'][0]['Tasks'][0]

        # Set Env variables
        task['Env']['FEDERATED_ROUNDS'] = str(user_conf['configuration']['rounds'])
        task['Env']['FEDERATED_METRIC'] = user_conf['configuration']['metric']
        task['Env']['FEDERATED_MIN_CLIENTS'] = str(user_conf['configuration']['min_clients'])
        task['Env']['FEDERATED_STRATEGY'] = user_conf['configuration']['strategy']

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
