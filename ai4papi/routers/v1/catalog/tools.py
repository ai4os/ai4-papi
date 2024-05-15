from copy import deepcopy
import json
import types

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException
import requests

from ai4papi import quotas
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags



@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_list(self):
    """
    Retrieve a list of *all* modules.

    This is implemented in a separate function as many functions from this router
    are using this function, so we need to avoid infinite recursions.
    """

    return list(papiconf.TOOLS.keys())


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_metadata(
    self,
    item_name: str,
    ):
    """
    Get the module's full metadata.
    """
    # Get default branch
    tools_branches= {
        'deep-oc-federated-server': 'main',
    }
    branch = tools_branches[item_name]

    # Retrieve metadata from that branch
    # Use try/except to avoid that a single module formatting error could take down
    # all the Dashboard
    metadata_url = f"https://raw.githubusercontent.com/deephdc/{item_name}/{branch}/metadata.json"

    try:
        r = requests.get(metadata_url)
        metadata = json.loads(r.text)

    except Exception:
        metadata = {
            "title": item_name,
            "summary": "",
            "description": [
                "The metadata of this module could not be retrieved probably due to a ",
                "JSON formatting error from the module maintainer."
            ],
            "keywords": [],
            "license": "",
            "date_creation": "",
            "sources": {
                "dockerfile_repo": f"https://github.com/deephdc/{item_name}",
                "docker_registry_repo": f"deephdc/{item_name}",
                "code": "",
            }
        }

    # Format "description" field nicely for the Dashboards Markdown parser
    metadata["description"] = "\n".join(metadata["description"])

    return metadata


def get_config(
    self,
    item_name: str,
    vo: str,
    ):
    """
    Returns the default configuration (dict) for creating a deployment
    for a specific module. It is prefilled with the appropriate
    docker image and the available docker tags.
    """
    # Retrieve tool configuration
    try:
        conf = deepcopy(papiconf.TOOLS[item_name]['user']['full'])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available tool.",
            )

    # Add available Docker tags
    tags = retrieve_docker_tags(item_name)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify the resources limits for a given user or VO
    conf["hardware"] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )
    
    return conf



Tools = Catalog()
Tools.get_list = types.MethodType(get_list, Tools)
Tools.get_config = types.MethodType(get_config, Tools)
Tools.get_metadata = types.MethodType(get_metadata, Tools)


router = APIRouter(
    prefix="/tools",
    tags=["Tools catalog"],
    responses={404: {"description": "Not found"}},
)
router.add_api_route(
    "/",
    Tools.get_filtered_list,
    methods=["GET"],
    )
router.add_api_route(
    "/detail",
    Tools.get_summary,
    methods=["GET"],
    )
router.add_api_route(
    "/tags",
    Tools.get_tags,
    methods=["GET"],
    )
router.add_api_route(
    "/{item_name}/metadata",
    Tools.get_metadata,
    methods=["GET"],
    )
router.add_api_route(
    "/{item_name}/config",
    Tools.get_config,
    methods=["GET"],
    )
