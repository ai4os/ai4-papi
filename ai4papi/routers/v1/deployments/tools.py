from copy import deepcopy
import datetime
import os
import json
import re
import secrets
import subprocess
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
from ai4papi.routers.v1 import deployments as ai4_deployments


router = APIRouter(
    prefix="/tools",
    tags=["Deployments (tools)"],
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
    vos = set(vos).intersection(set(papiconf.MAIN_CONF["auth"]["VO"]))
    if not vos:
        raise HTTPException(
            status_code=401,
            detail=f"Your VOs do not match available VOs: {papiconf.MAIN_CONF['auth']['VO']}.",
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
    auth.check_authorization(auth_info, vo)

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
        # Remove useless endpoints (they all point to same url)
        ignore = ["server", "grafana"]
        job["endpoints"] = {
            k: v for k, v in job["endpoints"].items() if k not in ignore
        }
        if job["active_endpoints"]:
            job["active_endpoints"] = [
                k for k in job["active_endpoints"] if k not in ignore
            ]

    if tool_id == "ai4os-ai4life-loader":
        job["main_endpoint"] = "ui"  # instead of deepaas

    if tool_id == "ai4os-nvflare":
        # Remove useless endpoints (they are not meant to be opened by the user directly)
        ignore = ["server-admin", "server-fl"]
        job["endpoints"] = {
            k: v for k, v in job["endpoints"].items() if k not in ignore
        }
        if job["active_endpoints"]:
            job["active_endpoints"] = [
                k for k in job["active_endpoints"] if k not in ignore
            ]

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
    auth.check_authorization(auth_info, vo)

    # Check tool_ID
    if tool_name not in Tools_catalog.get_items().keys():
        raise HTTPException(
            status_code=400,
            detail="This ID does not correspond to an available tool.",
        )

    # Check if your are allowed to deploy the tool
    restrictions = {"ai4os-llm": ["vo.imagine-ai.eu"]}
    if vo in restrictions.get(tool_name, []):
        raise HTTPException(
            status_code=403,
            detail="Your VO doesn't allow to deploy this tool.",
        )

    # Load tool configuration
    nomad_conf = deepcopy(papiconf.TOOLS[tool_name]["nomad"])
    user_conf = deepcopy(papiconf.TOOLS[tool_name]["user"]["values"])
    # TODO: given that some parts of the configuration are dynamically generated
    # (eg. model_id in ai4life/vllm) we should read "user_conf" from the catalog
    # We have to apply conversion to only keep the values
    # Same goes for modules

    # Update values conf in case we received a submitted conf
    if conf is not None:
        user_conf = utils.update_values_conf(
            submitted=conf,
            reference=user_conf,
        )

    # Utils validate conf
    user_conf = utils.validate_conf(user_conf)

    # Check if the provided configuration is within the job quotas
    # We only do this for tools that have a "hardware" section in the conf
    if "hardware" in user_conf.keys():
        quotas.check_jobwise(
            conf=user_conf,
            vo=vo,
            item_name=tool_name,
        )

    # Check if requested hardware is within the user total quota (summing modules and
    # tools)
    tools_deps = get_deployments(
        vos=[vo],
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials  # token
        ),
    )
    modules_deps = ai4_deployments.modules.get_deployments(
        vos=[vo],
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials  # token
        ),
    )
    quotas.check_userwise(
        conf=user_conf,
        deployments=modules_deps + tools_deps,
    )

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = str(uuid.uuid1())

    # Jobs from tutorial users should have low priority (ie. can be displaced if needed)
    if vo == "training.egi.eu":
        priority = 25
    else:
        priority = 50

    base_domain = papiconf.MAIN_CONF["lb"]["domain"][vo]

    if tool_name == "ai4os-dev-env":
        # Retrieve MLflow credentials
        user_secrets = ai4secrets.get_secrets(
            vo=vo,
            subpath="/services",
            authorization=types.SimpleNamespace(
                credentials=authorization.credentials,
            ),
        )
        mlflow_credentials = user_secrets.get("/services/mlflow/credentials", {})

        # Check IDE password length
        if len(user_conf["general"]["jupyter_password"]) < 9:
            raise HTTPException(
                status_code=400,
                detail="Your IDE needs a password of at least 9 characters.",
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
                "TITLE": user_conf["general"]["title"][:45],
                "DESCRIPTION": user_conf["general"]["desc"][:1000],
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "DOCKER_IMAGE": user_conf["general"]["docker_image"],
                "DOCKER_TAG": user_conf["general"]["docker_tag"],
                "SERVICE": user_conf["general"]["service"],
                "CPU_NUM": user_conf["hardware"]["cpu_num"],
                "RAM": user_conf["hardware"]["ram"],
                "DISK": user_conf["hardware"]["disk"],
                "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
                # Limit at 50% of RAM memory, in bytes
                "GPU_NUM": user_conf["hardware"]["gpu_num"],
                "GPU_MODELNAME": user_conf["hardware"]["gpu_type"],
                "JUPYTER_PASSWORD": user_conf["general"]["jupyter_password"],
                "RCLONE_CONFIG_RSHARE_URL": user_conf["storage"]["rclone_url"],
                "RCLONE_CONFIG_RSHARE_VENDOR": user_conf["storage"]["rclone_vendor"],
                "RCLONE_CONFIG_RSHARE_USER": user_conf["storage"]["rclone_user"],
                "RCLONE_CONFIG_RSHARE_PASS": user_conf["storage"]["rclone_password"],
                "RCLONE_CONFIG": user_conf["storage"]["rclone_conf"],
                "MLFLOW_USERNAME": mlflow_credentials.get("username", ""),
                "MLFLOW_PASSWORD": mlflow_credentials.get("password", ""),
                "MLFLOW_URI": papiconf.MAIN_CONF["mlflow"][vo],
                "MAILING_TOKEN": os.getenv("MAILING_TOKEN", default=""),
                "PROJECT_NAME": papiconf.MAIN_CONF["nomad"]["namespaces"][vo].upper(),
                "TODAY": str(datetime.date.today()),
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

        tasks = nomad_conf["TaskGroups"][0]["Tasks"]
        usertask = [t for t in tasks if t["Name"] == "main"][0]

        # Modify the GPU section
        if user_conf["hardware"]["gpu_num"] <= 0:
            # Delete GPU section in CPU deployments
            usertask["Env"]["NVIDIA_VISIBLE_DEVICES"] = "none"
            usertask["Resources"]["Devices"] = None
        else:
            # If gpu_type not provided, remove constraint to GPU model
            if not user_conf["hardware"]["gpu_type"]:
                usertask["Resources"]["Devices"][0]["Constraints"] = None

        # If storage credentials not provided, remove all storage-related tasks
        rclone = {
            k: v for k, v in user_conf["storage"].items() if k.startswith("rclone")
        }
        if not all(rclone.values()):
            exclude_tasks = ["storage_mount", "storage_cleanup", "dataset_download"]
        else:
            # Obscure rclone password on behalf of user
            obscured = subprocess.run(
                [f"rclone obscure {user_conf['storage']['rclone_password']}"],
                shell=True,
                capture_output=True,
                text=True,
            )
            usertask["Env"]["RCLONE_CONFIG_RSHARE_PASS"] = obscured.stdout.strip()

            # If datasets provided, replicate 'dataset_download' task as many times as needed
            if user_conf["storage"]["datasets"]:
                download_task = [t for t in tasks if t["Name"] == "dataset_download"][0]
                for i, dataset in enumerate(user_conf["storage"]["datasets"]):
                    t = deepcopy(download_task)
                    t["Env"]["DOI"] = dataset["doi"]
                    t["Env"]["FORCE_PULL"] = dataset["doi"]
                    t["Name"] = f"dataset_download_{i}"
                    tasks.append(t)
            # Always exclude initial 'dataset_download' task, as it is used as template
            exclude_tasks = ["dataset_download"]

        tasks[:] = [t for t in tasks if t["Name"] not in exclude_tasks]

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
                "CODE_CARBON": user_conf["general"]["co2"],
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
                "FEDERATED_ROUNDS": user_conf["flower"]["rounds"],
                "FEDERATED_METRIC": user_conf["flower"]["metric"],
                "FEDERATED_MIN_FIT_CLIENTS": user_conf["flower"]["min_fit_clients"],
                "FEDERATED_MIN_AVAILABLE_CLIENTS": user_conf["flower"][
                    "min_available_clients"
                ],
                "FEDERATED_STRATEGY": user_conf["flower"]["strategy"],
                "MU_FEDPROX": user_conf["flower"]["mu"],
                "FEDAVGM_SERVER_FL": user_conf["flower"]["fl"],
                "FEDAVGM_SERVER_MOMENTUM": user_conf["flower"]["momentum"],
                "DP": user_conf["flower"]["dp"],
                "METRIC_PRIVACY": user_conf["flower"]["mp"],
                "NOISE_MULT": user_conf["flower"]["noise_mult"],
                "SAMPLED_CLIENTS": user_conf["flower"]["sampled_clients"],
                "CLIP_NORM": user_conf["flower"]["clip_norm"],
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
        # Enforce having NVFLARE credentials
        if not (user_conf["nvflare"]["username"] and user_conf["nvflare"]["password"]):
            raise HTTPException(
                status_code=400,
                detail="You must provide credentials for NVFLARE.",
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
                "TITLE": user_conf["general"]["title"][:45],
                "DESCRIPTION": user_conf["general"]["desc"][:1000],
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "CPU_NUM": user_conf["hardware"]["cpu_num"],
                "RAM": user_conf["hardware"]["ram"],
                "DISK": user_conf["hardware"]["disk"],
                "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
                "NVFL_VERSION": "2.5-Stifo",
                "NVFL_USERNAME": user_conf["nvflare"]["username"],
                "NVFL_PASSWORD": user_conf["nvflare"]["password"],
                "NVFL_SERVER1": "%s-server.${meta.domain}-%s" % (job_uuid, base_domain),
                "NVFL_SHORTNAME": job_uuid[:16],
                "NVFL_APP_LOCATION": user_conf["nvflare"]["app_location"],
                "NVFL_STARTING_DATE": user_conf["nvflare"]["starting_date"],
                "NVFL_END_DATE": user_conf["nvflare"]["end_date"],
                "NVFL_PUBLIC_PROJECT": user_conf["nvflare"]["public_project"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

    # Deploy a OpenWebUI+vllm tool
    elif tool_name == "ai4os-llm":
        vllm_args = []

        if user_conf["llm"]["type"] == "open-webui":
            # Check if user has provided OpenAPI key/url
            if not (
                user_conf["llm"]["openai_api_key"]
                and user_conf["llm"]["openai_api_url"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail="You need to define an OpenAI key and url to deploy Open WebUI as standalone.",
                )
            api_token = user_conf["llm"]["openai_api_key"]
            api_endpoint = user_conf["llm"]["openai_api_url"]

        if user_conf["llm"]["type"] in ["openwebui", "both"]:
            # Check if user has provided a username
            if not user_conf["llm"]["ui_username"]:
                raise HTTPException(
                    status_code=400,
                    detail="A username is required to deploy this tool.",
                )
            # Check if user has provided a password
            if not user_conf["llm"]["ui_password"]:
                raise HTTPException(
                    status_code=400,
                    detail="A password is required to deploy this tool.",
                )

        if user_conf["llm"]["type"] in ["vllm", "both"]:
            # Create a OpenAPI key secret for the vLLM deployment
            api_token = secrets.token_hex()
            _ = ai4secrets.create_secret(
                vo=vo,
                secret_path=f"deployments/{job_uuid}/llm/vllm",
                secret_data={"token": api_token},
                authorization=SimpleNamespace(
                    credentials=authorization.credentials,
                ),
            )
            api_endpoint = (
                f"https://vllm-{job_uuid}" + ".${meta.domain}" + f"-{base_domain}/v1"
            )

            # Configure VLLM args
            model_id = user_conf["llm"]["vllm_model_id"]
            vllm_args += ["--model", model_id]
            vllm_args += papiconf.VLLM["models"][model_id]["args"]

            # Check if HF token is needed
            if (
                papiconf.VLLM["models"][model_id]["needs_HF_token"]
                and not user_conf["llm"]["HF_token"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail="This model requires a valid Huggingface token for deployment.",
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
                "TITLE": user_conf["general"]["title"][:45],
                "DESCRIPTION": user_conf["general"]["desc"][:1000],
                "BASE_DOMAIN": base_domain,
                "HOSTNAME": job_uuid,
                "VLLM_ARGS": json.dumps(vllm_args),
                "API_TOKEN": api_token,
                "API_ENDPOINT": api_endpoint,
                "HUGGINGFACE_TOKEN": user_conf["llm"]["HF_token"],
                "OPEN_WEBUI_USERNAME": user_conf["llm"]["ui_username"],
                "OPEN_WEBUI_PASSWORD": user_conf["llm"]["ui_password"],
            }
        )

        # Convert template to Nomad conf
        nomad_conf = nomad.load_job_conf(nomad_conf)

        # Define what to exclude
        if user_conf["llm"]["type"] == "vllm":
            exclude_tasks = ["open-webui", "create-admin"]
            exclude_services = ["ui"]
        elif user_conf["llm"]["type"] == "open-webui":
            exclude_tasks = ["vllm", "check_vllm_startup"]
            exclude_services = ["vllm"]
        else:
            exclude_tasks, exclude_services = [], []

        tasks = nomad_conf["TaskGroups"][0]["Tasks"]
        tasks[:] = [t for t in tasks if t["Name"] not in exclude_tasks]

        services = nomad_conf["TaskGroups"][0]["Services"]
        services[:] = [s for s in services if s["PortLabel"] not in exclude_services]

        # Rename first task as main task
        t = tasks[0]
        t["Name"] = "main"

    # Deploy AI4Life tool
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
    auth.check_authorization(auth_info, vo)

    # Delete deployment
    r = nomad.delete_deployment(
        deployment_uuid=deployment_uuid,
        namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
        owner=auth_info["id"],
    )

    # Remove Vault secrets belonging to that deployment
    user_secrets = ai4secrets.get_secrets(
        vo=vo,
        subpath=f"/deployments/{deployment_uuid}",
        authorization=SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )
    for path in user_secrets.keys():
        _ = ai4secrets.delete_secret(
            vo=vo,
            secret_path=path,
            authorization=SimpleNamespace(
                credentials=authorization.credentials,
            ),
        )

    return r
