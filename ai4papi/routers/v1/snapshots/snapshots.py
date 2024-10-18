import os
from types import SimpleNamespace
import types
from typing import Tuple, Union
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
from copy import deepcopy
from harborapi import HarborAsyncClient
import nomad as nomadAPI
import uuid
import datetime

from ai4papi import auth
from ai4papi.routers.v1 import deployments
import ai4papi.conf as papiconf
import ai4papi.nomad.common as nomad
import ai4papi.nomad.patches as nomad_patches

router = APIRouter(
    prefix="/snapshots",
    tags=["Snapshots deployments"],
    responses={404: {"description": "Not found"}},
)

client = HarborAsyncClient(
    url="https://registry.services.ai4os.eu/api/v2.0/",
    username='robot$user-snapshots+snapshot-api',
    secret=os.environ.get('HARBOR_ROBOT_PASSWORD', None),
)

Nomad = nomadAPI.Nomad()
Nomad.job.get_allocations = types.MethodType(nomad_patches.get_allocations, Nomad.job)

security = HTTPBearer()


@router.get("/")
async def get_snapshots(authorization=Depends(security)):
    auth_info = auth.get_user_info(token=authorization.credentials)

    vos = set(auth_info['vos']).intersection(set(papiconf.MAIN_CONF['auth']['VO']))

    projects = await client.get_repositories(project_name='user-snapshots')

    user_projects = list(
        filter(
            lambda p: p.name.replace('user-snapshots/', '').replace('_at_', '@')
            == auth_info['id'],
            projects,
        )
    )

    snapshots = await client.get_artifacts(
        project_name='user-snapshots',
        repository_name=user_projects[0].name.replace('user-snapshots/', ''),
    )

    snapshots = list(
        map(
            lambda s: {
                'Name': s.tags[0].name,
                'Labels': s.extra_attrs.config['Labels'],
            },
            snapshots,
        )
    )

    nomad_jobs = get_snapshots_jobs(
        authorization=SimpleNamespace(credentials=authorization.credentials), vos=vos
    )

    r = []

    for j in nomad_jobs:
        alloc = Nomad.job.get_allocations(namespace='ai4eosc', id_=j['snapshot_job_id'])

        size_status = alloc[0]['TaskStates']['check-container-size']['State']
        size_error = alloc[0]['TaskStates']['check-container-size']['Failed']
        upload_status = alloc[0]['TaskStates']['upload-image-registry']['State']
        upload_error = alloc[0]['TaskStates']['upload-image-registry']['Failed']

        if size_error or upload_error:  # Custom error message
            if size_error:
                error_message = "Container exceeded size limit"
            elif upload_error:
                error_message = "Upload failed. Please contact support."
            else:
                error_message = "Server error. Please contact support."

            r.append(
                {
                    'Snapshot ID': j['snapshot_label'],
                    'Target Job ID': j['snapshot_label'],
                    'Status': 'Failed',
                    'Error Message': error_message,
                    'Snapshot time': j['snapshot_date'],
                }
            )
        elif size_status == 'running' or upload_status == 'running':
            r.append(
                {
                    'Snapshot ID': j['snapshot_label'],
                    'Target Job ID': j['snapshot_label'],
                    'Status': 'In progress',
                    'Error Message': None,
                    'Snapshot time': j['snapshot_date'],
                }
            )

    for s in snapshots:
        r.append(
            {
                'Snapshot ID': s['Name'],
                'Target Job ID': s['Name'].split('_')[0],
                'Status': 'Completed',
                'Error Message': None,
                'Snapshot time': s['Labels']['DATE'],
            }
        )

    return r


@router.post("/")
async def create_snapshot(
    target: str = Query(..., description="The target job UUID"),
    authorization=Depends(security),
):
    """
    Submit a snapshot deployment to Nomad.

    Returns a string with the endpoint to access the API.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

    # Load module configuration
    nomad_conf = deepcopy(papiconf.SNAPSHOTS['nomad'])

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Get target job info
    for vo in auth_info['vos']:
        # Retrieve all jobs in namespace
        job = deployments.modules.get_deployment(
            vo=vo,
            deployment_uuid=target,
            full_info=True,
            authorization=SimpleNamespace(credentials=authorization.credentials),
        )

        if job:
            break

    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job {target} not found in any of the user VOs.",
        )

    allocation_info = Nomad.allocation.get_allocation(id_=job['alloc_ID'])

    timestamp = datetime.datetime.now().strftime("%s")

    # Replace the Nomad job template
    nomad_conf = nomad_conf.safe_substitute(
        {
            'JOB_UUID': job_uuid,
            'OWNER': auth_info['id'],
            'NAMESPACE': papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            'OWNER_NAME': auth_info['name'],
            'OWNER_EMAIL': auth_info['email'],
            'HOSTNAME': job_uuid,
            'TARGET_JOB_ID': target,
            'TARGET_NODE_ID': allocation_info['NodeID'],
            'FORMATED_OWNER': auth_info['id'].replace('@', '_at_'),
            'TITLE': job['title'],
            'DESCRIPTION': job['description'],
            'SNAPSHOT_DATE': datetime.datetime.now().strftime("%Y-%m-%d %X"),
            'TIMESTAMP': timestamp,
            'HARBOR_ROBOT_PASSWORD': os.environ.get('HARBOR_ROBOT_PASSWORD', None),
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    # Submit job
    r = nomad.create_deployment(nomad_conf)

    return r


@router.delete("/")
async def delete_snapshot(authorization=Depends(security), snapshot: str = Query(...)):
    auth_info = auth.get_user_info(token=authorization.credentials)

    await client.delete_artifact(
        project_name='user-snapshots',
        repository_name=auth_info['id'].replace('@', '_at_'),
        reference=snapshot,
    )

    return {"message": f"Snapshot {snapshot} deleted."}


def get_snapshots_jobs(
    vos: Union[Tuple, None] = Query(default=None),
    authorization=Depends(security),
):
    auth_info = auth.get_user_info(token=authorization.credentials)

    # If no VOs, then retrieve jobs from all user VOs
    # Always remove VOs that do not belong to the project
    if not vos:
        vos = auth_info['vos']
    vos = set(vos).intersection(set(papiconf.MAIN_CONF['auth']['VO']))
    if not vos:
        raise HTTPException(
            status_code=401,
            detail=f"The provided Virtual Organizations do not match with any of your available VOs: {auth_info['vos']}.",
        )

    user_jobs = []
    for vo in vos:
        # Retrieve all jobs in namespace
        jobs = get_deployments(
            namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
            owner=auth_info['id'],
            prefix='snapshot',
        )

        # Retrieve info for jobs in namespace
        for j in jobs:
            try:
                job_info = Nomad.job.get_job(
                    id_=j['ID'],
                    namespace=papiconf.MAIN_CONF['nomad']['namespaces'][vo],
                )
            except HTTPException:
                continue
            except Exception as e:
                raise (e)

            user_jobs.append(job_info)

    return list(map(lambda j: j['Meta'], user_jobs))


def get_deployments(
    namespace: str,
    owner: str,
    prefix: str = "",
):
    """
    Returns a list of all deployments belonging to a user, in a given namespace.
    """
    job_filter = (
        f'Name matches "^{prefix}" and '
        + 'Meta is not empty and '
        + f'Meta.owner == "{owner}"'
    )
    jobs = Nomad.jobs.get_jobs(namespace=namespace, filter_=job_filter)
    return jobs
