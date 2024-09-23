from copy import deepcopy
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth
import ai4papi.conf as papiconf
from ai4papi.routers.v1.catalog.modules import Modules
from ai4papi.routers.v1.stats.deployments import get_cluster_stats
import ai4papi.nomad.common as nomad


router = APIRouter(
    prefix="/nomad",
    tags=["Nomad trials"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


@router.post("/")
def create_deployment(
    module_name: str,
    authorization=Depends(security),
    ):
    """
    Submit a try-me deployment to Nomad.
    The deployment will automatically kill himself after a short amount of time.

    This endpoint is meant to be public for everyone to try (no authorization required).
    We deploy jobs by default in the AI4EOSC namespace.

    Returns a string with the endpoint to access the API.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

    # Retrieve docker_image from module_name
    meta = Modules.get_metadata(module_name)
    docker_image = meta['sources']['docker_registry_repo']

    # Load module configuration
    nomad_conf = deepcopy(papiconf.TRY_ME['nomad'])

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Replace the Nomad job template
    nomad_conf = nomad_conf.safe_substitute(
        {
            'JOB_UUID': job_uuid,
            'NAMESPACE': 'ai4eosc',  # (!) try-me jobs are always deployed in "ai4eosc"
            'OWNER': auth_info['id'],
            'OWNER_NAME': auth_info['name'],
            'OWNER_EMAIL': auth_info['email'],
            'BASE_DOMAIN': papiconf.MAIN_CONF['lb']['domain']['vo.ai4eosc.eu'],  # idem
            'HOSTNAME': job_uuid,
            'DOCKER_IMAGE': docker_image,
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    # Check that the target node (ie. tag='tryme') resources are available because
    # these jobs cannot be left queueing
    # We check for every resource metric (cpu, disk, ram)
    stats = get_cluster_stats(vo='vo.ai4eosc.eu')
    resources = ['cpu', 'ram', 'disk']
    keys = [f"{i}_used" for i in resources] + [f"{i}_total" for i in resources]
    status = {k: 0 for k in keys}

    for _, datacenter  in stats['datacenters'].items():
        for _, node in datacenter['nodes'].items():
            if 'tryme' in node['tags'] and node['status'] == 'ready':
                for k in keys:
                    status[k] += node[k]
    for r in resources:
        if status[f"{r}_total"] == 0 or status[f"{r}_used"] / status[f"{r}_total"] > 0.85:
            # We cut of somehow earlier than 100% because we are only accounting for
            # cores consumed in "main" task. But UI task is also consuming resources.
            raise HTTPException(
                status_code=503,
                detail="Sorry, but there seem to be no resources available right " \
                    "now to test the module. Please try later.",
                )

    # Check that the user hasn't too many "try-me" jobs currently running
    jobs = nomad.get_deployments(
        namespace="ai4eosc",  # (!) try-me jobs are always deployed in "ai4eosc"
        owner=auth_info['id'],
        prefix="try",
    )
    if len(jobs) >= 2:
        raise HTTPException(
            status_code=503,
            detail="Sorry, but you seem to be currently running two `Try-me` environments already. " \
                "Before launching a new one, you will need to wait till one of your " \
                "existing environments gets automatically deleted (ca. 10 min)."
            )

    # Submit job
    r = nomad.create_deployment(nomad_conf)

    return r


@router.get("/{deployment_uuid}")
def get_deployment(
    deployment_uuid: str,
    authorization=Depends(security),
    ):
    """
    This function is used mainly to be able to retrieve the endpoint of the try_me job.
    We cannot return the endpoint when creating the job, because the final endpoint will
    on which datacenter the job ends up landing.

    Parameters:
    * **deployment_uuid**: uuid of deployment to gather info about

    Returns a dict with info
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

    job = nomad.get_deployment(
        deployment_uuid=deployment_uuid,
        namespace="ai4eosc",  # (!) try-me jobs are always deployed in "ai4eosc"
        owner=auth_info['id'],
        full_info=True,
    )

    # Rewrite main endpoint, otherwise it automatically selects DEEPaaS API
    job['main_endpoint'] = 'ui'

    return job
