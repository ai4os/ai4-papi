"""
Get information from the API (no authentication needed)
"""

from copy import deepcopy

from fastapi import APIRouter, HTTPException
import requests

from ai4eosc.conf import USER_CONF
from ..flaat_impl import flaat


router = APIRouter(
    prefix="/info",
    tags=["info"],
    responses={404: {"description": "Not found"}},
)


@router.get("/conf")
@flaat.is_authenticated()
def get_default_deployment_conf(
):
    """
    Returns default configuration for creating a generic deployment.

    Returns a dict.
    """
    return USER_CONF


@router.get("/conf/{module_name}")
@flaat.is_authenticated()
def get_default_deployment_conf(
        module_name: str,
):
    """
    Returns the default configuration for creating a deployment
    for a specific module. It is prefilled with the appropriate
    docker image and the available docker tags.

    Returns a dict.
    """
    conf = deepcopy(USER_CONF)

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"deephdc/{module_name.lower()}"

    # Add available Docker tags
    url = f"https://registry.hub.docker.com/v2/repositories/deephdc/{module_name.lower()}/tags"
    try:
        r = requests.get(url)
        r.raise_for_status()
        r = r.json()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve Docker tags from {module_name}.",
            )

    tags = [i["name"] for i in r["results"]]
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    return conf
