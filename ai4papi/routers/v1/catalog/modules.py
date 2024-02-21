import configparser
from copy import deepcopy
import re

from cachetools import cached, TTLCache
from fastapi import APIRouter
import requests

from ai4papi import quotas, nomad
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags



@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_list(
    ):
    """
    Retrieve a list of *all* modules.

    This is implemented in a separate function as many functions from this router
    are using this function, so we need to avoid infinite recursions.
    """

    gitmodules_url = "https://raw.githubusercontent.com/deephdc/deep-oc/master/.gitmodules"
    r = requests.get(gitmodules_url)

    cfg = configparser.ConfigParser()
    cfg.read_string(r.text)

    # Convert 'submodule "DEEP-OC-..."' --> 'deep-oc-...'
    modules = [
        re.search(r'submodule "(.*)"', s).group(1).lower() for s in cfg.sections()
        ]

    return modules


def get_config(
    item_name: str,
    vo: str,
):
    """
    Returns the default configuration (dict) for creating a deployment
    for a specific module. It is prefilled with the appropriate
    docker image and the available docker tags.
    """
    #TODO: We are not checking if module exists in the marketplace because
    # we are treating each route as independent. In the future, this can
    # be done as an API call to the other route.

    conf = deepcopy(papiconf.MODULES['user']['full'])

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"deephdc/{item_name}"

    # Add available Docker tags
    tags = retrieve_docker_tags(item_name)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Use VS Code (Coder OSS) in the development container
    if item_name == 'deep-oc-generic-dev':
        conf["general"]["service"]["value"] = 'vscode'
        conf["general"]["service"]["options"].insert(0, 'vscode')
        conf["general"]["service"]["options"].remove('deepaas')  # no models installed in dev

    # Modify the resources limits for a given user or VO
    conf['hardware'] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Fill with available GPU models in the cluster
    models = nomad.common.get_gpu_models()
    if models:
        conf["hardware"]["gpu_type"]["options"] += models

    return conf



Modules = Catalog()
Modules.get_list = get_list
Modules.get_config = get_config


router = APIRouter(
    prefix="/modules",
    tags=["Modules catalog"],
    responses={404: {"description": "Not found"}},
)
router.add_api_route(
    "/",
    Modules.get_filtered_list,
    methods=["GET"],
    )
router.add_api_route(
    "/detail",
    Modules.get_summary,
    methods=["GET"],
    )
router.add_api_route(
    "/tags",
    Modules.get_tags,
    methods=["GET"],
    )
router.add_api_route(
    "/{item_name}/metadata",
    Modules.get_metadata,
    methods=["GET"],
    )
router.add_api_route(
    "/{item_name}/config",
    Modules.get_config,
    methods=["GET"],
    )
