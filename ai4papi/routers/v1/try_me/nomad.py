from copy import deepcopy
import uuid

from fastapi import APIRouter
from fastapi.security import HTTPBearer

from ai4papi import utils
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
    ):
    """
    Submit a try-me deployment to Nomad.
    The deployment will automatically kill himself after a short amount of time.

    This endpoint is meant to be public for everyone to try (no authorization required).
    We deploy jobs by default in the AI4EOSC namespace.

    Returns a string with the endpoint to access the API.
    """
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
            'DOMAIN': domain,
            'DOCKER_IMAGE': docker_image,
        }
    )

    # Convert template to Nomad conf
    nomad_conf = nomad.load_job_conf(nomad_conf)

    # Submit job
    r = nomad.create_deployment(nomad_conf)

    return r


# TODO: implement a get method to retrieve endpoint
# This is implemented in a separate method because we cannot know what is the final
# endpoint before knowing in which datacenter it has landed
