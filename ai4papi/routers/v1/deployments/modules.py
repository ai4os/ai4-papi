from copy import deepcopy
import datetime
import os
import types
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer

from ai4papi import auth, module_patches, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad
from ai4papi.routers import v1
from ai4papi.routers.v1 import secrets as ai4secrets


router = APIRouter(
    prefix="/modules",
    tags=["Modules deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


# When deploying in production, force the definition of a provenance token
provenance_token = os.environ.get("PAPI_PROVENANCE_TOKEN", None)
if not papiconf.IS_DEV and not provenance_token:
    raise Exception('You need to define the variable "PAPI_PROVENANCE_TOKEN".')


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
            prefix="module",
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
            except HTTPException:  # not a module
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
    # Check if the query comes from the provenance-workflow, if so search in snapshots
    if authorization.credentials == provenance_token:
        return utils.retrieve_from_snapshots(deployment_uuid)

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

    # Check the deployment is indeed a module
    if not job["name"].startswith("module"):
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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Load module configuration
    nomad_conf = deepcopy(papiconf.MODULES["nomad"])
    user_conf = deepcopy(papiconf.MODULES["user"]["values"])

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
    if vo == "training.egi.eu":
        priority = 25
    else:
        priority = 50

    base_domain = papiconf.MAIN_CONF["lb"]["domain"][vo]

    # Retrieve MLflow credentials
    secrets = ai4secrets.get_secrets(
        vo=vo,
        subpath="/services",
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )
    mlflow_credentials = secrets.get("/services/mlflow/credentials", {})

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

    # Apply patches if needed
    usertask = module_patches.patch_nextcloud_mount(
        user_conf["general"]["docker_image"], usertask
    )

    # Modify the GPU section
    if user_conf["hardware"]["gpu_num"] <= 0:
        # Delete GPU section in CPU deployments
        usertask["Resources"]["Devices"] = None
    else:
        # If gpu_type not provided, remove constraint to GPU model
        if not user_conf["hardware"]["gpu_type"]:
            usertask["Resources"]["Devices"][0]["Constraints"] = None

    # If the image belong to Harbor, then it's a user snapshot
    docker_image = user_conf["general"]["docker_image"]
    if docker_image.split("/")[0] == "registry.services.ai4os.eu":
        # Check the user is the owner of the image
        if docker_image.split("/")[-1] != auth_info["id"].replace("@", "_at_"):
            raise HTTPException(
                status_code=401,
                detail="You are not the owner of the Harbor image.",
            )

        # Check the snapshot indeed exists
        user_snapshots = v1.snapshots.get_harbor_snapshots(
            owner=auth_info["id"],
            vo=vo,
        )
        snapshot_ids = [s["snapshot_ID"] for s in user_snapshots]
        if user_conf["general"]["docker_tag"] not in snapshot_ids:
            raise HTTPException(
                status_code=400,
                detail="The snapshot does not exist.",
            )

        # Add Harbor authentication credentials to Nomad job
        usertask["Config"]["auth"] = [
            {
                "username": papiconf.HARBOR_USER,
                "password": papiconf.HARBOR_PASS,
            }
        ]

    # If storage credentials not provided, remove all storage-related tasks
    rclone = {k: v for k, v in user_conf["storage"].items() if k.startswith("rclone")}
    if not all(rclone.values()):
        exclude_tasks = ["storage_mount", "storage_cleanup", "dataset_download"]
    else:
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

    # If DEEPaaS was not launched, do not launch UI because it will fail
    if user_conf["general"]["service"] != "deepaas":
        exclude_tasks.append("ui")

    tasks[:] = [t for t in tasks if t["Name"] not in exclude_tasks]

    # Remove appropriate Traefik domains in each case (no need to remove the ports)
    services = nomad_conf["TaskGroups"][0]["Services"]
    if user_conf["general"]["service"] == "deepaas":
        exclude_services = ["ide"]
    else:
        exclude_services = ["ui"]
    services[:] = [s for s in services if s["PortLabel"] not in exclude_services]

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

    return r
