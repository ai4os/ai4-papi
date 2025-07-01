from copy import deepcopy
import datetime
import json
import os
import subprocess
import types
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.security import HTTPBearer

from ai4papi import auth, module_patches, quotas, utils
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad
from ai4papi.routers import v1
from ai4papi.routers.v1 import secrets as ai4secrets


router = APIRouter(
    prefix="/batch",
    tags=["Modules batch deployments"],
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
        # Retrieve all jobs in namespace (including dead jobs)
        job_filter = (
            'Name matches "^batch" and '
            + "Meta is not empty and "
            + f'Meta.owner == "{auth_info["id"]}"'
        )
        jobs = nomad.Nomad.jobs.get_jobs(
            namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
            filter_=job_filter,
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

    # Check the deployment is indeed a batch
    if not job["name"].startswith("batch"):
        raise HTTPException(
            status_code=400,
            detail="This deployment is not a batch job.",
        )

    return job


@router.post("")
def create_deployment(
    vo: str,
    user_cmd: UploadFile,
    conf: Union[str, None] = Form(None),
    authorization=Depends(security),
):
    """
    Submit a deployment to Nomad.

    Parameters:
    * **vo**: Virtual Organization where you want to create your deployment
    * **user_cmd**: batch command to execute in the batch deployment
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

    # Load module configuration
    # To avoid duplicating too much code, we use the standard job deployment
    # and then remove/replace the parts we don't need
    nomad_conf = deepcopy(papiconf.MODULES["nomad"])
    user_conf = deepcopy(papiconf.MODULES["user"]["values"])

    if conf is not None:
        # Configuration has to be received as a str then converted to dict.
        # Because otherwise we cannot have both content-type "application/json" and
        # content-type "multipart/form-data"
        # ref: https://fastapi.tiangolo.com/tutorial/request-forms-and-files/
        try:
            conf = json.loads(conf)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="conf parameter must be a valid JSON string",
            )

        # Update values conf in case we received a submitted conf
        user_conf = utils.update_values_conf(submitted=conf, reference=user_conf)

    # Validate conf
    user_conf = utils.validate_conf(user_conf)

    # Check if the provided configuration is within the job quotas
    quotas.check_jobwise(conf=user_conf, vo=vo)

    # Check if requested hardware is within the user total quota (only summing batch
    # jobs)
    batch_deps = get_deployments(
        vos=[vo],
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials  # token
        ),
    )
    quotas.check_userwise(conf=user_conf, deployments=batch_deps)

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Jobs from tutorial users should have low priority (ie. can be displaced if needed)
    priority = 25 if vo == "training.egi.eu" else 50

    # Retrieve MLflow credentials
    user_secrets = ai4secrets.get_secrets(
        vo=vo,
        subpath="/services",
        authorization=types.SimpleNamespace(
            credentials=authorization.credentials,
        ),
    )
    mlflow_credentials = user_secrets.get("/services/mlflow/credentials", {})

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
            "DOCKER_IMAGE": user_conf["general"]["docker_image"],
            "DOCKER_TAG": user_conf["general"]["docker_tag"],
            "CPU_NUM": user_conf["hardware"]["cpu_num"],
            "RAM": user_conf["hardware"]["ram"],
            "DISK": user_conf["hardware"]["disk"],
            "SHARED_MEMORY": user_conf["hardware"]["ram"] * 10**6 * 0.5,
            # Limit at 50% of RAM memory, in bytes
            "GPU_NUM": user_conf["hardware"]["gpu_num"],
            "GPU_MODELNAME": user_conf["hardware"]["gpu_type"],
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
        usertask["Env"]["NVIDIA_VISIBLE_DEVICES"] = "none"
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

    # Enforce storage credentials, otherwise batch jobs cannot save their outputs
    rclone = {k: v for k, v in user_conf["storage"].items() if k.startswith("rclone")}
    if not all(rclone.values()):
        raise HTTPException(
            status_code=401,
            detail="You must provide a storage when running batch jobs.",
        )

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

    # Batch jobs do not need UI
    exclude_tasks.append("ui")

    tasks[:] = [t for t in tasks if t["Name"] not in exclude_tasks]

    # Remove all endpoints
    del nomad_conf["TaskGroups"][0]["Services"]
    del nomad_conf["TaskGroups"][0]["Networks"][0]["DynamicPorts"]
    del usertask["Config"]["ports"]

    # Replace the standard job service (eg. deepaas) with the commands provided
    # by the user
    usertask["Config"]["command"] = "/bin/bash"
    usertask["Config"]["args"] = ["/srv/user-batch-commands.sh"]
    usertask["Config"]["mount"] = [
        {
            "source": "local/batch.sh",
            "readonly": False,
            "type": "bind",
            "target": "/srv/user-batch-commands.sh",
        }
    ]
    content = user_cmd.file.readlines()
    content = " ".join([line.decode("utf-8") for line in content])  # bytes to utf-8
    usertask["Templates"] = [{"DestPath": "local/batch.sh", "EmbeddedTmpl": content}]

    # Batch jobs should no longer have "type=compute" constraint (ie. module's old constraint)
    nomad_conf["Constraints"][:] = [
        c
        for c in nomad_conf["Constraints"]
        if not c == {"LTarget": "${meta.type}", "Operand": "=", "RTarget": "compute"}
    ]
    # Batch jobs should be able to deploy both in "type=batch" OR "type=compute"
    nomad_conf["Constraints"].append(
        {
            "LTarget": "${meta.type}",
            "Operand": "set_contains_any",
            "RTarget": "compute,batch",
        }
    )
    # Batch jobs should not prefer cpu nodes because batch is meant for GPU training
    # (also messes with next affinity)
    nomad_conf["Affinities"][:] = [
        c
        for c in nomad_conf["Affinities"]
        if not c
        == {
            "LTarget": "${meta.tags}",
            "Operand": "regexp",
            "RTarget": "cpu",
            "Weight": 100,
        }
    ]
    # Batch jobs should have affinity for batch nodes (even if they can also be deployed
    # in compute nodes)
    nomad_conf["Affinities"].append(
        {"LTarget": "${meta.type}", "Operand": "=", "RTarget": "batch", "Weight": 100}
    )

    # Do not restart if user commands if fail
    usertask["RestartPolicy"] = {"Attempts": 0, "Mode": "fail"}

    # Change the job type to batch mode
    nomad_conf["Type"] = "batch"
    nomad_conf["Name"] = nomad_conf["Name"].replace("module-", "batch-")

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

    return r
