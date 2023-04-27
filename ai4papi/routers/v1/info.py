"""Misc routes.

Methods returning the conf are authenticated in order to be able to fill the `Virtual
Organization` field.
"""

from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
import requests

from ai4papi.auth import get_user_info
from ai4papi.conf import USER_CONF


router = APIRouter(
    prefix="/info",
    tags=["info"],
    responses={404: {"description": "Not found"}},
)

security = Depends(HTTPBearer())

base_mod_url = "https://registry.hub.docker.com/v2/repositories/deephdc/"


@router.get("/conf/{module_name}")
def get_default_deployment_conf(module_name: str, authorization=security):
    """
    Return the default configuration to creating a deployment for a module.

    It is prefilled with the appropriate docker image and the available docker tags.

    We are not checking if module exists in the marketplace because we are treating each
    route as independent. In the future, this can be done as an API call to the other
    route.
    """
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)
    vos = auth_info["vo"]

    # Generate the conf
    conf = deepcopy(USER_CONF)

    # Fill the Virtual Organization
    conf["general"]["virtual_organization"]["value"] = vos[0]
    conf["general"]["virtual_organization"]["options"] = vos

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"deephdc/{module_name.lower()}"

    # Add available Docker tags
    url = f"{base_mod_url}/{module_name.lower()}/tags"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        r = r.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve Docker tags from {module_name}.",
        )

    tags = [i["name"] for i in r["results"]]
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    return conf
