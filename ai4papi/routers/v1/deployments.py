"""API routest that manage deployments with Nomad."""

# Notes:
# * Terminology warning: what we call a "deployment" (as in `create_deployment`) is a
#   Nomad "job" (not a Nomad "deployment"!)

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
import nomad
from nomad.api import exceptions

from ai4papi.auth import get_user_info
from ai4papi.conf import NOMAD_JOB_CONF, USER_CONF_VALUES


router = APIRouter(
    prefix="/deployments",
    tags=["deployments"],
    responses={404: {"description": "Not found"}},
)

security = Depends(HTTPBearer())

Nomad = nomad.Nomad()


@router.get("/")
def get_deployments(authorization=security):
    """Return a list of all deployments belonging to a user."""
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)
    owner, provider = auth_info["id"], auth_info["vo"]  # noqa(F841)

    # Filter jobs
    jobs = Nomad.jobs.get_jobs()  # job summaries
    njobs = []
    for j in jobs:
        # Skip deleted jobs
        if j["Status"] == "dead":
            continue

        # Get full job description
        j = Nomad.job.get_job(j["ID"])

        # Remove jobs not belonging to owner
        if j["Meta"] and (owner == j["Meta"].get("owner", "")):
            njobs.append(j)

    # Retrieve info for jobs
    fjobs = []
    for j in njobs:

        job_info = get_deployment(
            deployment_uuid=j["ID"],
            authorization=SimpleNamespace(
                credentials=authorization.credentials  # token
            ),
        )

        fjobs.append(job_info)

    return fjobs


@router.get("/{deployment_uuid}")
def get_deployment(deployment_uuid: str, authorization=security):
    """
    Retrieve the info of a specific deployment.

    Format outputs to a Nomad-independent format to be used by the Dashboard

    Parameters:
    * **deployment_uuid**: uuid of deployment to gather info about

    Returns a dict with info
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)
    owner, provider = auth_info["id"], auth_info["vo"]  # noqa(F841)

    # Check the deployment exists
    try:
        j = Nomad.job.get_job(deployment_uuid)
    except exceptions.URLNotFoundNomadException:
        raise HTTPException(
            status_code=403,
            detail="No deployment exists with this uuid.",
        )

    # Check job does belong to owner
    if j["Meta"] and owner != j["Meta"].get("owner", ""):
        raise HTTPException(
            status_code=403,
            detail="You are not the owner of that deployment.",
        )

    # Create job info dict
    info = {
        "job_ID": j["ID"],
        "status": j["Status"],
        "owner": j["Meta"]["owner"],
        "title": j["Meta"]["title"],
        "description": j["Meta"]["description"],
        "docker_image": None,
        "submit_time": datetime.fromtimestamp(j["SubmitTime"] // 1000000000).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),  # nanoseconds to timestamp
        "resources": {},
        "endpoints": {},
        "alloc_ID": None,
    }

    # Retrieve Docker image
    for t in j["TaskGroups"][0]["Tasks"]:
        if t["Name"] == "usertask":
            info["docker_image"] = t["Config"]["image"]

    # Only fill (resources + endpoints) if the job is allocated
    allocs = Nomad.job.get_allocations(j["ID"])
    if allocs:
        # only keep the first allocation per job
        a = Nomad.allocation.get_allocation(allocs[0]["ID"])

        info["alloc_ID"] = a["ID"]

        res = a["AllocatedResources"]
        devices = res["Tasks"]["usertask"]["Devices"]
        gpu_num = sum([1 for d in devices if d["Type"] == "gpu"]) if devices else 0
        info["resources"] = {
            "cpu_num": res["Tasks"]["usertask"]["Cpu"]["CpuShares"],
            "gpu_num": gpu_num,
            "memoryMB": res["Tasks"]["usertask"]["Memory"]["MemoryMB"],
            "diskMB": res["Shared"]["DiskMB"],
        }

        public_ip = "https://xxx.xxx.xxx.xxx"  # todo: replace when ready
        ports = a["Resources"]["Networks"][0]["DynamicPorts"]
        info["endpoints"] = {d["Label"]: f"{public_ip}:{d['Value']}" for d in ports}
        # TODO: We need to connect internal IP (172.XXX) to external IP (193.XXX)
        # (Traefik + Consul Connect) use service discovery to map internal ip to
        # external IPs???

    else:
        # Something happened, job didn't deploy (eg. jobs needs port that's currently
        # being used)
        # We have to return `placement failures message`.
        evals = Nomad.job.get_evaluations(j["ID"])
        info["error_msg"] = f"{evals[0]['FailedTGAllocs']}"
        # TODO: improve this error message once we start seeing the different modes of
        # failures in typical cases

    return info


@router.post("/")
def create_deployment(conf: dict | None = None, authorization=security):
    """
    Submit a deployment to Nomad.

    Parameters:
    * **conf**: configuration dict of the deployment to be submitted.
    If only a partial configuration is submitted, the remaining will be
    filled with default args.

    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)
    owner, provider = auth_info["id"], auth_info["vo"]  # noqa(F841)

    # Update default dict with new values
    job_conf, user_conf = deepcopy(NOMAD_JOB_CONF), deepcopy(USER_CONF_VALUES)
    if conf:
        user_conf.update(conf)

    # Enforce JupyterLab password minimum number of characters
    if (
        user_conf["general"]["service"] == "jupyterlab"
        and len(user_conf["general"]["jupyter_password"]) < 9
    ):
        raise HTTPException(
            status_code=501,
            detail="JupyterLab password should have at least 9 characters.",
        )

    # Assign unique job ID (if submitting job with existing ID, the existing job gets
    # replaced)
    # id is generated from (MAC address+timestamp) so it's unique
    job_conf["ID"] = f"userjob-{uuid.uuid1()}"

    # Add owner and extra information to the job metadata
    job_conf["Meta"]["owner"] = owner
    # keep only 45 first characters
    job_conf["Meta"]["title"] = user_conf["general"]["title"][:45]
    # limit to 1K characters
    job_conf["Meta"]["description"] = user_conf["general"]["desc"][:1000]

    # Replace user conf in Nomad job
    task = job_conf["TaskGroups"][0]["Tasks"][0]

    img = f"{user_conf['general']['docker_image']}:{user_conf['geneal']['docker_tag']}"
    task["Config"]["image"] = img
    task["Config"]["command"] = "deep-start"
    task["Config"]["args"] = [f"--{user_conf['general']['service']}"]

    # TODO: add `docker_privileged` arg if we still need it

    task["Resources"]["CPU"] = user_conf["hardware"]["cpu_num"]
    task["Resources"]["MemoryMB"] = user_conf["hardware"]["ram"]
    task["Resources"]["DiskMB"] = user_conf["hardware"]["disk"]
    if user_conf["hardware"]["gpu_num"] <= 0:
        del task["Resources"]["Devices"]
    else:
        task["Resources"]["Devices"][0]["Count"] = user_conf["hardware"]["gpu_num"]
        if not user_conf["hardware"]["gpu_type"]:
            del task["Resources"]["Devices"][0]["Affinities"]
        else:
            task["Resources"]["Devices"][0]["Affinities"][0]["RTarget"] = user_conf[
                "hardware"
            ]["gpu_type"]

    task["Env"]["RCLONE_CONFIG_RSHARE_URL"] = user_conf["storage"]["rclone_url"]
    task["Env"]["RCLONE_CONFIG_RSHARE_VENDOR"] = user_conf["storage"]["rclone_vendor"]
    task["Env"]["RCLONE_CONFIG_RSHARE_USER"] = user_conf["storage"]["rclone_user"]
    task["Env"]["RCLONE_CONFIG_RSHARE_PASS"] = user_conf["storage"]["rclone_password"]
    task["Env"]["RCLONE_CONFIG"] = user_conf["storage"]["rclone_conf"]
    task["Env"]["jupyterPASSWORD"] = user_conf["general"]["jupyter_password"]

    # Submit job
    try:
        response = Nomad.jobs.register_job({"Job": job_conf})  # noqa(F841)
        return {
            "status": "success",
            "job_id": job_conf["ID"],
        }
    except Exception as e:
        return {
            "status": "fail",
            "error_msg": str(e),
        }


@router.delete("/{deployment_uuid}")
def delete_deployment(deployment_uuid: str, authorization=security):
    """Delete a deployment.

    Users can only delete their own deployments.

    Parameters:
    * **deployment_uuid**: uuid of deployment to delete

    Returns a dict with status
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)
    owner, provider = auth_info["id"], auth_info["vo"]  # noqa(F841)

    # Check the deployment exists
    try:
        j = Nomad.job.get_job(deployment_uuid)
    except exceptions.URLNotFoundNomadException:
        raise HTTPException(
            status_code=403,
            detail="No deployment exists with this uuid.",
        )

    # Check job does belong to owner
    if j["Meta"] and owner != j["Meta"].get("owner", ""):
        raise HTTPException(
            status_code=403,
            detail="You are not the owner of that deployment.",
        )

    # Delete deployment
    Nomad.job.deregister_job(deployment_uuid)

    return {"status": "success"}
