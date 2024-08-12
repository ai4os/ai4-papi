from copy import deepcopy
import uuid

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer

from ai4papi import auth
import ai4papi.conf as papiconf
from ai4papi.routers.v1.catalog.modules import Modules
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
    # docker_image = "deephdc/image-classification-tf"  # todo: remove

    # Load module configuration
    nomad_conf = deepcopy(papiconf.TRY_ME['nomad'])

    # Generate UUID from (MAC address+timestamp) so it's unique
    job_uuid = uuid.uuid1()

    # Generate a domain for user-app and check nothing is running there
    domain = utils.generate_domain(
        hostname='',
        base_domain=papiconf.MAIN_CONF['lb']['domain']['vo.ai4eosc.eu'],
        job_uuid=job_uuid,
    )
    utils.check_domain(domain)

    # Replace the Nomad job template
    nomad_conf = nomad_conf.safe_substitute(
        {
            'JOB_UUID': job_uuid,
            'OWNER': auth_info['id'],
            'OWNER_NAME': auth_info['name'],
            'OWNER_EMAIL': auth_info['email'],
            'DOCKER_IMAGE': docker_image,
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

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
        namespace="ai4eosc",
        owner=auth_info['id'],
        full_info=True,
    )

    return job
