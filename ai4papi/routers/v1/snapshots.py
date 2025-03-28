"""
Make snapshots to Harbor from Nomad deployments.

The strategy for saving in Harbor is:
* 1 user = 1 Docker image
* 1 snapshot = 1 Docker label (in that image)
  --> labels follow the naming "{NOMAD_UUID_{TIMESTAMP}"
"""

from copy import deepcopy
import datetime
from typing import Tuple, Union
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
from harborapi import HarborClient
from nomad.api import exceptions

from ai4papi import auth
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad_common


router = APIRouter(
    prefix="/snapshots",
    tags=["Snapshots of deployments"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

# Init the Harbor client
if papiconf.HARBOR_USER and papiconf.HARBOR_PASS:
    client = HarborClient(
        url="https://registry.services.ai4os.eu/api/v2.0/",
        username=papiconf.HARBOR_USER,
        secret=papiconf.HARBOR_PASS,
    )
else:
    client = None

# Use the Nomad cluster inited in nomad.common
Nomad = nomad_common.Nomad


@router.get("")
def get_snapshots(
    vos: Union[Tuple, None] = Query(default=None),
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
    # Always remove VOs that do not belong to the project
    if not vos:
        vos = auth_info["vos"]
    vos = set(vos).intersection(set(papiconf.MAIN_CONF["auth"]["VO"]))
    if not vos:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organizations do not match with any of your available VOs: {auth_info['vos']}.",
        )

    snapshots = []
    for vo in vos:
        # Retrieve the completed snapshots from Harbor
        snapshots += get_harbor_snapshots(
            owner=auth_info["id"],
            vo=vo,
        )

        # Retrieve pending/failed snapshots from Nomad
        snapshots += get_nomad_snapshots(
            owner=auth_info["id"],
            vo=vo,
        )

    return snapshots


@router.post("")
def create_snapshot(
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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]

    # Check the user is within our limits
    snapshots = get_harbor_snapshots(
        owner=auth_info["id"],
        vo=vo,
    )
    total_size = sum([s["size"] for s in snapshots])
    if total_size > (10 * 10**9):
        raise HTTPException(
            status_code=401,
            detail="You have exceeded the 10 GB quota. Please delete some snapshots before creating a new one.",
        )

    # Load module configuration
    nomad_conf = deepcopy(papiconf.SNAPSHOTS["nomad"])

    # Get target job info
    job_info = nomad_common.get_deployment(
        deployment_uuid=deployment_uuid,
        namespace=namespace,
        owner=auth_info["id"],
        full_info=False,
    )
    if job_info["status"] != "running":
        raise HTTPException(
            status_code=401,
            detail='You cannot make a snapshot of a job that has a status different than "running".',
        )

    # Get the allocation info
    allocation_info = Nomad.allocation.get_allocation(id_=job_info["alloc_ID"])

    # Replace the Nomad job template
    now = datetime.datetime.now()
    nomad_conf = nomad_conf.safe_substitute(
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
            "VO": vo,
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad_common.load_job_conf(nomad_conf)

    # Submit job
    _ = nomad_common.create_deployment(nomad_conf)

    return {
        "status": "success",
        "snapshot_ID": f"{deployment_uuid}_{now.strftime('%s')}",
    }


@router.delete("")
def delete_snapshot(
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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Check is the snapshot is in the "completed" list (Harbor)
    snapshots = get_harbor_snapshots(
        owner=auth_info["id"],
        vo=vo,
    )
    snapshot_ids = [s["snapshot_ID"] for s in snapshots]
    if snapshot_uuid in snapshot_ids:
        _ = client.delete_artifact(
            project_name="user-snapshots",
            repository_name=auth_info["id"].replace("@", "_at_"),
            reference=snapshot_uuid,
        )
        return {"status": "success"}

    # Check if the snapshot is in the "in progress" list (Nomad)
    snapshots = get_nomad_snapshots(
        owner=auth_info["id"],
        vo=vo,
    )
    snapshot_ids = [s["snapshot_ID"] for s in snapshots]
    if snapshot_uuid in snapshot_ids:
        idx = snapshot_ids.index(snapshot_uuid)

        # Check the deployment exists
        try:
            j = Nomad.job.get_job(
                id_=snapshots[idx]["nomad_ID"],
                namespace=papiconf.MAIN_CONF["nomad"]["namespaces"][vo],
            )
        except exceptions.URLNotFoundNomadException:
            raise HTTPException(
                status_code=400,
                detail="No deployment exists with this uuid.",
            )

        # Check job does belong to owner
        if j["Meta"] and auth_info["id"] != j["Meta"].get("owner", ""):
            raise HTTPException(
                status_code=400,
                detail="You are not the owner of that deployment.",
            )

        # Delete deployment
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


def get_harbor_snapshots(
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
    projects = client.get_repositories(project_name="user-snapshots")
    users = [p.name.split("/")[1] for p in projects]
    user_str = owner.replace("@", "_at_")
    if user_str not in users:
        return []

    # Retrieve the snapshots
    artifacts = client.get_artifacts(
        project_name="user-snapshots",
        repository_name=user_str,
    )
    snapshots = []
    for a in artifacts:
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
                "docker_image": f"registry.services.ai4os.eu/user-snapshots/{user_str}",
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
    jobs = Nomad.jobs.get_jobs(
        namespace=namespace,
        filter_=job_filter,
    )

    # Retrieve info for those jobs
    # user_jobs = []
    snapshots = []
    for j in jobs:
        # Get job to retrieve the metadata
        job_info = Nomad.job.get_job(
            id_=j["ID"],
            namespace=namespace,
        )

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
        allocs = Nomad.job.get_allocations(
            namespace=namespace,
            id_=j["ID"],
        )

        # Reorder allocations based on recency
        dates = [a["CreateTime"] for a in allocs]
        allocs = [
            x
            for _, x in sorted(
                zip(dates, allocs),
                key=lambda pair: pair[0],
            )
        ][::-1]  # more recent first

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
                "The deployment is too big to make a snapshot. Please delete some data to make it lighter."
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
