from copy import deepcopy
import json

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException
import secrets
import requests

from ai4papi import quotas
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_items(
    ):
    # Set default branch manually (because we are not yet reading this from submodules)
    tools_branches= {
        'deep-oc-federated-server': 'main',
    }

    tools = {}
    for k in papiconf.TOOLS.keys():
        tools[k] = {
            'url': f'https://github.com/deephdc/{k}',
            'branch': tools_branches[k],
        }

    return tools


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_metadata(
    item_name: str,
    ):
    # Check if item is in the items list
    items = get_items()
    if item_name not in items.keys():
        raise HTTPException(
            status_code=400,
            detail=f"Item {item_name} not in catalog: {items.keys()}",
            )

    # Retrieve metadata from default branch
    # Use try/except to avoid that a single module formatting error could take down
    # all the Dashboard
    branch = items[item_name].get("branch", "master")
    url = items[item_name]['url'].replace('github.com', 'raw.githubusercontent.com')
    metadata_url = f"{url}/{branch}/metadata.json"
    try:
        r = requests.get(metadata_url)
        metadata = json.loads(r.text)
    except Exception:
        print(f'Error parsing metadata: {item_name}')
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
    metadata = get_metadata(item_name)

    # Add available Docker tags
    registry = metadata['sources']['docker_registry_repo']
    repo = registry.split('/')[0]
    if repo not in ['deephdc', 'ai4oshub']:
        repo = 'deephdc'
    tags = retrieve_docker_tags(image=item_name, repo=repo)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify the resources limits for a given user or VO
    conf["hardware"] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Extra tool-dependent steps
    if item_name == 'deep-oc-federated-server':
        # Create unique secret for that federated server
        conf["general"]["federated_secret"]["value"] = secrets.token_hex()

    return conf


Tools = Catalog()
Tools.get_items = get_items
Tools.get_config = get_config
Tools.get_metadata = get_metadata  # TODO: inherit the common one, because it's the same for modules and tools

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
