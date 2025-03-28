from copy import deepcopy
import types

from fastapi import APIRouter, HTTPException
from natsort import natsorted

from ai4papi import quotas, nomad
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


def get_config(
    self,
    item_name: str,
    vo: str,
):
    # Check if module exists
    modules = self.get_items()
    if item_name not in modules.keys():
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available module.",
        )

    # Retrieve module configuration
    conf = deepcopy(papiconf.MODULES["user"]["full"])

    # Retrieve module metadata
    metadata = self.get_metadata(item_name)

    # Parse docker registry
    registry = metadata["links"]["docker_image"]
    repo, image = registry.split("/")[-2:]
    if repo not in ["deephdc", "ai4oshub"]:
        repo = "ai4oshub"

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

    # Add available Docker tags
    tags = retrieve_docker_tags(image=image, repo=repo)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Custom conf for development environment
    if item_name == "ai4os-dev-env":
        # For dev-env, order the tags in "Z-A" order instead of "newest"
        # This is done because builds are done in parallel, so "newest" is meaningless
        # (Z-A + natsort) allows to show more recent semver first
        tags = natsorted(tags)[::-1]
        conf["general"]["docker_tag"]["options"] = tags
        conf["general"]["docker_tag"]["value"] = tags[0]

        # Use VS Code (Coder OSS) in the development container
        conf["general"]["service"]["value"] = "vscode"
        conf["general"]["service"]["options"].insert(0, "vscode")
        conf["general"]["service"]["options"].remove(
            "deepaas"
        )  # no models installed in dev

    # Modify the resources limits for a given user or VO
    conf["hardware"] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Fill with available GPU models in the cluster
    models = nomad.common.get_gpu_models(vo)
    if models:
        conf["hardware"]["gpu_type"]["options"] += models

    return conf


Modules = Catalog(
    repo="ai4os-hub/modules-catalog",
    item_type="module",
)
Modules.get_config = types.MethodType(get_config, Modules)


router = APIRouter(
    prefix="/modules",
    tags=["Modules catalog"],
    responses={404: {"description": "Not found"}},
)
router.add_api_route(
    "",
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
    deprecated=True,
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

router.add_api_route(
    "/{item_name}/refresh",
    Modules.refresh_metadata_cache_entry,
    methods=["PUT"],
)
