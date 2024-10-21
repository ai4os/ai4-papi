from copy import deepcopy
import types

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException

from ai4papi import quotas
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_items(self):
    # Set default branch manually (because we are not yet reading this from submodules)
    # TODO: start reading from submodules (only accept the submodules that have been
    # integrated in papiconf.TOOLS)
    tools_branches= {
        'ai4os-federated-server': 'main',
    }

    tools = {}
    for k in papiconf.TOOLS.keys():
        tools[k] = {
            'url': f'https://github.com/ai4os/{k}',
            'branch': tools_branches[k],
        }

    return tools


def get_config(
    self,
    item_name: str,
    vo: str,
    ):
    # Retrieve tool configuration
    try:
        conf = deepcopy(papiconf.TOOLS[item_name]['user']['full'])
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available tool.",
            )

    # Retrieve tool metadata
    metadata = self.get_metadata(item_name)

    # Parse docker registry
    registry = metadata['links']['docker_image']
    repo, image = registry.split('/')[-2:]
    if repo not in ['deephdc', 'ai4oshub']:
        repo = 'ai4oshub'

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

    # Add available Docker tags
    tags = retrieve_docker_tags(image=image, repo=repo)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify the resources limits for a given user or VO
    conf["hardware"] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )

    return conf


Tools = Catalog()
Tools.get_items = types.MethodType(get_items, Tools)
Tools.get_config = types.MethodType(get_config, Tools)


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
    deprecated=True,
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
