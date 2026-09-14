"""
Make snapshots to Harbor from Nomad deployments.

The strategy for saving in Harbor is:
* 1 user = 1 Docker image
* 1 snapshot = 1 Docker label (in that image)
  --> labels follow the naming "{NOMAD_UUID_{TIMESTAMP}"

We use async functions since HarborAPI is natively an async client (non-async
functionalities do not work very well)
"""

import asyncio
import datetime
import uuid
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from harborapi import HarborAsyncClient
from starlette.concurrency import run_in_threadpool

import ai4papi.conf as papiconf
from ai4papi import auth, nomad_utils, schemas

router = APIRouter(
    prefix="/snapshots",
    tags=["Snapshots (deployments)"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

# Use the Nomad cluster inited in nomad utils
Nomad = nomad_utils.Nomad

# Define limits for snapshots size
INDIVIDUAL_LIMIT_GB = 10
TOTAL_LIMIT_GB = 15


def get_harbor_client() -> HarborAsyncClient | None:
    if papiconf.HARBOR_USER and papiconf.HARBOR_PASS:
        return HarborAsyncClient(
            url="https://registry.cloud.ai4eosc.eu/api/v2.0/",
            username=papiconf.HARBOR_USER,
            secret=papiconf.HARBOR_PASS,
        )
    return None


@router.get("")
async def get_snapshots(
    vos: schemas.VoList = None,
    authorization=Depends(security),
):
    """
    Get all your snapshots from Harbor/Nomad

    Parameters:
    * **vo**: Virtual Organizations from where you want to retrieve your deployments.
      If no vo is provided, it will retrieve the deployments of all VOs.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

    # If no VOs, then retrieve jobs from all user VOs
    if vos is None:
        user_vos = set(papiconf.MAIN_CONF["auth"]["VO"])
    else:
        # Always remove VOs that do not belong to the project
        user_vos = set(vos).intersection(set(papiconf.MAIN_CONF["auth"]["VO"]))
    if not user_vos:
        raise HTTPException(
            status_code=401,
            detail=f"Your VOs do not match available VOs: {papiconf.MAIN_CONF['auth']['VO']}.",
        )

    tasks = []
    async with asyncio.TaskGroup() as tg:
        for vo in user_vos:
            # Retrieve the completed snapshots from Harbor
            tasks.append(
                tg.create_task(get_harbor_snapshots(owner=auth_info["id"], vo=vo))
            )

            # Retrieve pending/failed snapshots from Nomad
            # Run blocking Nomad calls in threadpool so they don't block asyncio (since non async function)
            tasks.append(
                tg.create_task(
                    run_in_threadpool(
                        get_nomad_snapshots,
                        owner=auth_info["id"],
                        vo=vo,
                    )
                )
            )
    snapshots = [s for t in tasks for s in t.result()]

    return snapshots


@router.post("")
async def create_snapshot(
    vo: str,
    deployment_uuid: str,
    authorization=Depends(security),
):
    """
    Submit a Nomad job to make a snapshot from a container belonging to an existing job.

    Parameters:
    * **vo**: Virtual Organization where your deployment is located
    * **deployment_uuid**: uuid of deployment to make a snapshot of
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info, vo)

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    # Check the user is within our limits
    snapshots = await get_harbor_snapshots(owner=auth_info["id"], vo=vo)
    total_size = sum([s["size"] for s in snapshots])
    if total_size > (TOTAL_LIMIT_GB * 10**9):
        raise HTTPException(
            status_code=400,
            detail=(
                f"You have exceeded the {TOTAL_LIMIT_GB} GB quota. "
                "Please delete some snapshots before creating a new one."
            ),
        )

    # Load module configuration
    nomad_template = deepcopy(papiconf.SNAPSHOTS["nomad"])

    # Get target job info
    job_info = nomad_utils.get_deployment(
        deployment_uuid=deployment_uuid,
        namespace=namespace,
        owner=auth_info["id"],
        full_info=False,
    )
    if job_info["status"] != "running":
        raise HTTPException(
            status_code=400,
            detail='You cannot make a snapshot of a job that has a status different than "running".',
        )

    # Get the allocation info
    allocation_info = Nomad.allocation.get_allocation(id_=job_info["alloc_ID"])

    # Replace the Nomad job template
    now = datetime.datetime.now()
    nomad_conf_str = nomad_template.safe_substitute(
        {
            "JOB_UUID": uuid.uuid1(),
            "NAMESPACE": papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
            "OWNER": auth_info["id"],
            "OWNER_NAME": auth_info["name"],
            "OWNER_EMAIL": auth_info["email"],
            "TARGET_NODE_ID": allocation_info["NodeID"],
            "TARGET_JOB_ID": deployment_uuid,
            "FORMATTED_OWNER": auth_info["id"].replace("@", "_at_"),
            "TIMESTAMP": now.strftime("%s"),
            "TITLE": job_info["title"],
            "DESCRIPTION": job_info["description"],
            "SUBMIT_TIME": now.strftime("%Y-%m-%d %X"),
            "HARBOR_ROBOT_USER": papiconf.HARBOR_USER,
            "HARBOR_ROBOT_PASSWORD": papiconf.HARBOR_PASS,
            "SIZE_LIMIT_GB": INDIVIDUAL_LIMIT_GB,
            "VO": vo,
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad_utils.load_job_conf(nomad_conf_str)

    # Submit job
    _ = nomad_utils.create_deployment(nomad_conf)

    return {
        "status": "success",
        "snapshot_ID": f"{deployment_uuid}_{now.strftime('%s')}",
    }


@router.delete("")
async def delete_snapshot(
    vo: str,
    snapshot_uuid: str,
    authorization=Depends(security),
):
    """
    Delete a snapshot (either from Harbor or Nomad)

    Parameters:
    * **vo**: Virtual Organization where your deployment is located
    * **snapshot_uuid**: uuid of snapshot you want to delete
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info, vo)

    # Check is the snapshot is in the "completed" list (Harbor)
    client = get_harbor_client()
    snapshots = await get_harbor_snapshots(owner=auth_info["id"], vo=vo)
    snapshot_ids = [s["snapshot_ID"] for s in snapshots]
    if client and (snapshot_uuid in snapshot_ids):
        _ = await client.delete_artifact(
            project_name="user-snapshots",
            repository_name=auth_info["id"].replace("@", "_at_"),
            reference=snapshot_uuid,
        )
        return {"status": "success"}

    # Check if the snapshot is in the "in progress" list (Nomad)
    snapshots = get_nomad_snapshots(owner=auth_info["id"], vo=vo)
    snapshot_ids = [s["snapshot_ID"] for s in snapshots]
    if snapshot_uuid in snapshot_ids:
        idx = snapshot_ids.index(snapshot_uuid)
        Nomad.job.deregister_job(
            id_=snapshots[idx]["nomad_ID"],
            namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
            purge=True,
        )
        return {"status": "success"}

    # If it not in either of those two lists, then the UUID is wrong
    raise HTTPException(
        status_code=400,
        detail="The UUID does not correspond to any of your available snapshots.",
    )


async def get_harbor_snapshots(
    owner: str,
    vo: str,
):
    """
    Retrieve the completed snapshots from Harbor

    Parameters:
    * **owner**: EGI ID of the owner
    * **vo**: Virtual Organization the snapshot belongs to
    """
    # Check if the user exists in Harbor (ie. Docker image exists)
    client = get_harbor_client()
    if not client:
        return []
    repos = await client.get_repositories(project_name="user-snapshots")
    users = [r.name.split("/")[1] for r in repos]  # ty: ignore[not-iterable]
    user_str = owner.replace("@", "_at_")
    if user_str not in users:
        return []

    # Retrieve the snapshots
    artifacts = await client.get_artifacts(
        project_name="user-snapshots",
        repository_name=user_str,
    )
    snapshots = []
    for a in artifacts:  # ty: ignore[not-iterable]
        # Ignore snapshot if it doesn't belong to the VO
        a_labels = a.extra_attrs.root["config"]["Labels"]
        if a_labels.get("VO") != vo:
            continue

        snapshots.append(
            {
                "snapshot_ID": a.tags[0].name,
                "status": "complete",
                "error_msg": None,
                "submit_time": a_labels["DATE"],
                "size": a.size,  # bytes
                "title": a_labels["TITLE"],
                "description": a_labels["DESCRIPTION"],
                "nomad_ID": None,
                "docker_image": f"registry.cloud.ai4eosc.eu/user-snapshots/{user_str}",
            }
        )
    return snapshots


def get_nomad_snapshots(
    owner: str,
    vo: str,
):
    """
    Retrieve the snapshots in progress/failed from Nomad

    Parameters:
    * **owner**: EGI ID of the owner
    * **vo**: Virtual Organization the snapshot belongs to
    """
    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    # Retrieve snapshot jobs
    job_filter = (
        'Name matches "^snapshot" and '
        + "Meta is not empty and "
        + f'Meta.owner == "{owner}"'
    )
    jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_=job_filter)

    # Retrieve info for those jobs
    # user_jobs = []
    snapshots = []
    for j in jobs:
        # Get job to retrieve the metadata
        job_info = Nomad.job.get_job(id_=j["ID"], namespace=namespace)

        # Generate snapshot info template
        tmp = {
            "snapshot_ID": job_info["Meta"].get("snapshot_id"),
            "status": None,
            "error_msg": None,
            "submit_time": job_info["Meta"].get("submit_time"),
            "size": None,
            "title": None,
            "description": None,
            "nomad_ID": j["ID"],
            "docker_image": None,
        }

        # Get allocation to retrieve the task status
        allocs = Nomad.job.get_allocations(namespace=namespace, id_=j["ID"])

        # Reorder allocations based on recency
        dates = [a["CreateTime"] for a in allocs]
        allocs = [x for _, x in sorted(zip(dates, allocs), key=lambda pair: pair[0])]
        allocs = allocs[::-1]  # more recent first

        # Retrieve tasks
        tasks = (
            allocs[0]["TaskStates"] if allocs else {}
        )  # if no allocations, use empty dict
        tasks = tasks or {}  # if None, use empty dict
        client_status = allocs[0]["ClientStatus"] if allocs else None

        # Check status of both tasks and generate appropriate snapshot status/error
        size_status = tasks.get("check-container-size", {}).get("State", None)
        size_error = tasks.get("check-container-size", {}).get("Failed", False)
        upload_status = tasks.get("upload-image-registry", {}).get("State", None)
        upload_error = tasks.get("upload-image-registry", {}).get("Failed", False)

        if size_error:
            tmp["status"] = "failed"
            tmp["error_msg"] = (
                f"The deployment is too big to make a snapshot (maximum is {INDIVIDUAL_LIMIT_GB} GB). "
                "Please delete some data or move it to '/storage' to make the deployment lighter."
            )

        elif upload_error:
            tmp["status"] = "failed"
            tmp["error_msg"] = "Upload failed. Please contact support."

        elif size_status == "running" or upload_status == "running":
            tmp["status"] = "in progress"

        elif client_status == "pending" or (not size_status) or (not upload_status):
            tmp["status"] = "starting"

        else:
            # Avoid showing dead user jobs that completed correctly
            continue

        snapshots.append(tmp)

    return snapshots
