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
from ai4papi.routers.v1.catalog.tools import Tools as Tools_catalog
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
    # Always remove VOs that do not belong to the project
    if not vos:
        vos = auth_info["vos"]
    vos = set(vos).intersection(set(papiconf.MAIN_CONF["auth"]["VO"]))
    if not vos:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organizations do not match with any of your available VOs: {auth_info['vos']}.",
        )

    user_jobs = []
    for vo in vos:
        # Retrieve all jobs in namespace
        jobs = nomad.get_deployments(
            namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
            owner=auth_info["id"],
            prefix="tool",
        )

        # Retrieve info for jobs in namespace
        for j in jobs:
            try:
                job_info = get_deployment(
                    vo=vo,
                    deployment_uuid=j["ID"],
                    full_info=full_info,
                    authorization=types.SimpleNamespace(
                        credentials=authorization.credentials  # token
                    ),
                )
            except HTTPException:  # not a tool
                continue
            except Exception as e:  # unexpected error
                raise (e)

            user_jobs.append(job_info)

    # Sort deployments by creation date
    seq = [j["submit_time"] for j in user_jobs]
    args = sorted(range(len(seq)), key=seq.__getitem__)[::-1]
    sorted_jobs = [user_jobs[i] for i in args]

    return sorted_jobs


def remove_job_endpoints(job, endpoints=[]):
    """Remove useless endpoints (they all point to same url)"""
    job["endpoints"] = {k: v for k, v in job["endpoints"].items() if k not in endpoints}
    if job["active_endpoints"]:
        job["active_endpoints"] = [
            k for k in job["active_endpoints"] if k not in endpoints
        ]
    return job


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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    job = nomad.get_deployment(
        deployment_uuid=deployment_uuid,
        namespace=namespace,
        owner=auth_info["id"],
        full_info=full_info,
    )

    # Check the deployment is indeed a tool
    if not job["name"].startswith("tool"):
        raise HTTPException(
            status_code=400,
            detail="This deployment is not a tool.",
        )

    # Add an additional field with the tool type
    # We map name from Nomad job to tool ID
    match = re.search(r"tool-(.*?)-[a-f0-9-]{36}", job["name"])
    nomad_name = match.group(1) if match else ""
    tool_id = papiconf.tools_nomad2id.get(nomad_name, "")
    job["tool_name"] = tool_id

    # Additional checks
    if tool_id == "ai4os-cvat":
        job = remove_job_endpoints(job, endpoints=["server", "grafana"])

    if tool_id == "ai4os-ai4life-loader":
        job["main_endpoint"] = "ui"  # instead of deepaas

    return job


@router.post("")
def create_deployment(
    vo: str,
    tool_name: str,
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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Check tool_ID
    if tool_name not in Tools_catalog.get_items().keys():
        raise HTTPException(
            status_code=400,
            detail="This ID does not correspond to an available tool.",
        )

    # Load tool configuration
    nomad_conf = deepcopy(papiconf.TOOLS[tool_name]["nomad"])
    user_conf = deepcopy(papiconf.TOOLS[tool_name]["user"]["values"])

    # Update values conf in case we received a submitted conf
    if conf is not None:
        user_conf = utils.update_values_conf(
            submitted=conf,
            reference=user_conf,
        )

    # NVIDIA Flare
    # use the value of nvfl_dashboard_project_app_location for the docker_image parameter
    if tool_name == "ai4os-nvflare" and "docker_image" not in user_conf["general"]:
        user_conf["general"]["docker_image"] = user_conf["general"][
            "nvfl_dashboard_project_app_location"
        ]

    # Utils validate conf
    user_conf = utils.validate_conf(user_conf)

    # Check if the provided configuration is within the job quotas
    # Skip this check with CVAT because it does not have a "hardware" section in the conf
    if tool_name not in ["ai4os-cvat"]:
        quotas.check_jobwise(
            conf=user_conf,
            vo=vo,
            item_name=tool_name,
        )

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Jobs from tutorial users should have low priority (ie. can be displaced if needed)
    if vo == "training.egi.eu":
        priority = 25
    else:
        priority = 50

    base_domain = papiconf.MAIN_CONF["lb"]["domain"][vo]

    # Deploy a Federated server
    if tool_name == "ai4os-federated-server":
        # Create a default secret for the Federated Server
        _ = ai4secrets.create_secret(
            vo=vo,
            secret_path=f"deployments/{job_uuid}/federated/default",
            secret_data={"token": secrets.token_hex()},
            authorization=SimpleNamespace(
                credentials=authorization.credentials,
            ),
        )

        # Create a Vault token so that the deployment can access the Federated secret
        vault_token = ai4secrets.create_vault_token(
            jwt=authorization.credentials,
            issuer=auth_info["issuer"],
            ttl="365d",  # 1 year expiration date
        )

        # Replace the Nomad job template
        nomad_conf = nomad_conf.safe_substitute(
            {
                "JOB_UUID": job_uuid,
                "NAMESPACE": papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
                "PRIORITY": priority,
                "OWNER": auth_info["id"],
                "OWNER_NAME": auth_info["name"],
                "OWNER_EMAIL": auth_info["email"],
                "TITLE": user_conf["general"]["title"][
                    :45
                ],  # keep only 45 first characters
                "DESCRIPTION": user_conf["general"]["desc"][
                    :1000
                ],  # limit to 1K characters
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "DOCKER_IMAGE": user_conf["general"]["docker_image"],
                "DOCKER_TAG": user_conf["general"]["docker_tag"],
                "CPU_NUM": user_conf["hardware"]["cpu_num"],
                "RAM": user_conf["hardware"]["ram"],
                "DISK": user_conf["hardware"]["disk"],
                "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
                # Limit at 50% of RAM memory, in bytes
                "JUPYTER_PASSWORD": user_conf["general"]["jupyter_password"],
                "VAULT_TOKEN": vault_token,
                "FEDERATED_ROUNDS": user_conf["configuration"]["rounds"],
                "FEDERATED_METRIC": user_conf["configuration"]["metric"],
                "FEDERATED_MIN_FIT_CLIENTS": user_conf["configuration"][
                    "min_fit_clients"
                ],
                "FEDERATED_MIN_AVAILABLE_CLIENTS": user_conf["configuration"][
                    "min_available_clients"
                ],
                "FEDERATED_STRATEGY": user_conf["configuration"]["strategy"],
                "MU_FEDPROX": user_conf["configuration"]["mu"],
                "FEDAVGM_SERVER_FL": user_conf["configuration"]["fl"],
                "FEDAVGM_SERVER_MOMENTUM": user_conf["configuration"]["momentum"],
                "DP": user_conf["configuration"]["dp"],
                "NOISE_MULT": user_conf["configuration"]["noise_mult"],
                "SAMPLED_CLIENTS": user_conf["configuration"]["sampled_clients"],
                "CLIP_NORM": user_conf["configuration"]["clip_norm"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

        tasks = nomad_conf["TaskGroups"][0]["Tasks"]
        usertask = [t for t in tasks if t["Name"] == "main"][0]

        # Launch `deep-start` compatible service if needed
        service = user_conf["general"]["service"]
        if service in ["deepaas", "jupyter", "vscode"]:
            usertask["Config"]["command"] = "deep-start"
            usertask["Config"]["args"] = [f"--{service}"]

    # Deploy a CVAT tool
    elif tool_name == "ai4os-cvat":
        # Enforce defining CVAT username and password
        cvat = {
            k: v
            for k, v in user_conf["general"].items()
            if k in ["cvat_username", "cvat_password"]
        }
        if not all(cvat.values()):
            raise HTTPException(
                status_code=400,
                detail="You must fill all CVAT-related variables.",
            )

        # Enforce all rclone vars are defined
        rclone = {
            k: v for k, v in user_conf["storage"].items() if k.startswith("rclone")
        }
        if not all(rclone.values()):
            raise HTTPException(
                status_code=400,
                detail="You must fill all RCLONE-related variables.",
            )

        # Replace the Nomad job template
        job_title = re.sub(
            r'[<>:"/\\|?* ]',
            "_",
            user_conf["general"]["title"][:45],
        )  # make title foldername-friendly

        nomad_conf = nomad_conf.safe_substitute(
            {
                "JOB_UUID": job_uuid,
                "NAMESPACE": papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
                "PRIORITY": priority,
                "OWNER": auth_info["id"],
                "OWNER_NAME": auth_info["name"],
                "OWNER_EMAIL": auth_info["email"],
                "TITLE": user_conf["general"]["title"][
                    :45
                ],  # keep only 45 first characters
                "DESCRIPTION": user_conf["general"]["desc"][
                    :1000
                ],  # limit to 1K characters
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "CVAT_USERNAME": user_conf["general"]["cvat_username"],
                "CVAT_PASSWORD": user_conf["general"]["cvat_password"],
                "RESTORE_FROM": user_conf["storage"]["cvat_backup"],
                "BACKUP_NAME": f"{job_title}",
                "RCLONE_CONFIG_RSHARE_URL": user_conf["storage"]["rclone_url"],
                "RCLONE_CONFIG_RSHARE_VENDOR": user_conf["storage"]["rclone_vendor"],
                "RCLONE_CONFIG_RSHARE_USER": user_conf["storage"]["rclone_user"],
                "RCLONE_CONFIG_RSHARE_PASS": user_conf["storage"]["rclone_password"],
                "RCLONE_CONFIG": user_conf["storage"]["rclone_conf"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

    # Deploy an NVFlare Federated server and Dashboard
    elif tool_name == "ai4os-nvflare":
        # Replace the Nomad job template
        nomad_conf = nomad_conf.safe_substitute(
            {
                "JOB_UUID": job_uuid,
                "NAMESPACE": papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
                "PRIORITY": priority,
                "OWNER": auth_info["id"],
                "OWNER_NAME": auth_info["name"],
                "OWNER_EMAIL": auth_info["email"],
                "TITLE": user_conf["general"]["title"][
                    :45
                ],  # keep only 45 first characters
                "DESCRIPTION": user_conf["general"]["desc"][
                    :1000
                ],  # limit to 1K characters
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "CPU_NUM": user_conf["hardware"]["cpu_num"],
                "RAM": user_conf["hardware"]["ram"],
                "DISK": user_conf["hardware"]["disk"],
                "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
                # Limit at 50% of RAM memory, in bytes
                "NVFL_VERSION": user_conf["general"]["nvfl_version"],
                "NVFL_SERVER_JUPYTER_PASSWORD": user_conf["general"][
                    "nvfl_server_jupyter_password"
                ],
                "NVFL_DASHBOARD_USERNAME": user_conf["general"][
                    "nvfl_dashboard_username"
                ],
                "NVFL_DASHBOARD_PASSWORD": user_conf["general"][
                    "nvfl_dashboard_password"
                ],
                "NVFL_DASHBOARD_SERVER_SERVER1": "%s-server.${meta.domain}-%s"
                % (job_uuid, base_domain),
                "NVFL_DASHBOARD_SERVER_HA_MODE": False,
                "NVFL_DASHBOARD_SERVER_OVERSEER": "",
                "NVFL_DASHBOARD_SERVER_SERVER2": "",
                "NVFL_DASHBOARD_PROJECT_SHORT_NAME": user_conf["general"][
                    "nvfl_dashboard_project_short_name"
                ],
                "NVFL_DASHBOARD_PROJECT_TITLE": user_conf["general"][
                    "nvfl_dashboard_project_title"
                ],
                "NVFL_DASHBOARD_PROJECT_DESCRIPTION": user_conf["general"][
                    "nvfl_dashboard_project_description"
                ],
                "NVFL_DASHBOARD_PROJECT_APP_LOCATION": user_conf["general"][
                    "nvfl_dashboard_project_app_location"
                ],
                "NVFL_DASHBOARD_PROJECT_STARTING_DATE": user_conf["general"][
                    "nvfl_dashboard_project_starting_date"
                ],
                "NVFL_DASHBOARD_PROJECT_END_DATE": user_conf["general"][
                    "nvfl_dashboard_project_end_date"
                ],
                "NVFL_DASHBOARD_PROJECT_PUBLIC": user_conf["general"][
                    "nvfl_dashboard_project_public"
                ],
                "NVFL_DASHBOARD_PROJECT_FROZEN": user_conf["general"][
                    "nvfl_dashboard_project_frozen"
                ],
                "RCLONE_CONFIG_RSHARE_URL": user_conf["storage"]["rclone_url"],
                "RCLONE_CONFIG_RSHARE_VENDOR": user_conf["storage"]["rclone_vendor"],
                "RCLONE_CONFIG_RSHARE_USER": user_conf["storage"]["rclone_user"],
                "RCLONE_CONFIG_RSHARE_PASS": user_conf["storage"]["rclone_password"],
                "RCLONE_CONFIG": user_conf["storage"]["rclone_conf"],
                "RCLONE_REMOTE_PATH": user_conf["storage"]["rclone_remote_path"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

    # Deploy a CVAT tool
    elif tool_name == "ai4os-ai4life-loader":
        # Replace the Nomad job template
        nomad_conf = nomad_conf.safe_substitute(
            {
                "JOB_UUID": job_uuid,
                "NAMESPACE": papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
                "PRIORITY": priority,
                "OWNER": auth_info["id"],
                "OWNER_NAME": auth_info["name"],
                "OWNER_EMAIL": auth_info["email"],
                "TITLE": user_conf["general"]["title"][
                    :45
                ],  # keep only 45 first characters
                "DESCRIPTION": user_conf["general"]["desc"][
                    :1000
                ],  # limit to 1K characters
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "DOCKER_IMAGE": user_conf["general"]["docker_image"],
                "DOCKER_TAG": user_conf["general"]["docker_tag"],
                "AI4LIFE_MODEL": user_conf["general"]["model_id"],
                "CPU_NUM": user_conf["hardware"]["cpu_num"],
                "RAM": user_conf["hardware"]["ram"],
                "DISK": user_conf["hardware"]["disk"],
                "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
                # Limit at 50% of RAM memory, in bytes
                "GPU_NUM": user_conf["hardware"]["gpu_num"],
                "GPU_MODELNAME": user_conf["hardware"]["gpu_type"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

        tasks = nomad_conf["TaskGroups"][0]["Tasks"]
        usertask = [t for t in tasks if t["Name"] == "main"][0]

        # Modify the GPU section
        if user_conf["hardware"]["gpu_num"] <= 0:
            # Delete GPU section in CPU deployments
            usertask["Resources"]["Devices"] = None
        else:
            # If gpu_type not provided, remove constraint to GPU model
            if not user_conf["hardware"]["gpu_type"]:
                usertask["Resources"]["Devices"][0]["Constraints"] = None

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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Delete deployment
    r = nomad.delete_deployment(
        deployment_uuid=deployment_uuid,
        namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
        owner=auth_info["id"],
    )

    # Remove Vault secrets belonging to that deployment
    secrets = ai4secrets.get_secrets(
        vo=vo,
        subpath=f"/deployments/{deployment_uuid}",
        authorization=SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )
    for path in secrets.keys():
        _ = ai4secrets.delete_secret(
            vo=vo,
            secret_path=path,
            authorization=SimpleNamespace(
                credentials=authorization.credentials,
            ),
        )

    return r
