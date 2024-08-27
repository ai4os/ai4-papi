from copy import deepcopy
import types

from fastapi import APIRouter, HTTPException

from ai4papi import quotas
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


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
    registry = metadata['sources']['docker_registry_repo']
    repo, image = registry.split('/')[:2]
    if repo not in ['deephdc', 'ai4oshub']:
        repo = 'ai4oshub'

    # Fill with correct Docker image and tags (not needed for CVAT because hardcoded)
    if item_name in ['ai4os-federated-server']:
        conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

        tags = retrieve_docker_tags(image=image, repo=repo)
        conf["general"]["docker_tag"]["options"] = tags
        conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify the resources limits for a given user or VO
    if conf.get("hardware", None):
        conf["hardware"] = quotas.limit_resources(
            item_name=item_name,
            vo=vo,
        )

    return conf


Tools = Catalog(
    repo='ai4os/tools-catalog',
)
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
