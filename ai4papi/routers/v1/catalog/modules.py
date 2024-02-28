import configparser
from copy import deepcopy
import json

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException
import requests

from ai4papi import quotas, nomad
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_items(
    ):
    gitmodules_url = "https://raw.githubusercontent.com/deephdc/deep-oc/master/.gitmodules"
    r = requests.get(gitmodules_url)

    cfg = configparser.ConfigParser()
    cfg.read_string(r.text)

    modules = {}
    for section in cfg.sections():
        items = dict(cfg.items(section))
        key = items.pop('path').lower()
        modules[key] = items

    return modules


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
    # Check if module exists
    modules = get_items()
    if item_name not in modules.keys():
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available module.",
            )

    # Retrieve module configuration
    conf = deepcopy(papiconf.MODULES['user']['full'])

    # Retrieve module metadata
    metadata = get_metadata(item_name)

    # Parse docker registry
    registry = metadata['sources']['docker_registry_repo']
    repo, image = registry.split('/')[:2]
    if repo not in ['deephdc', 'ai4oshub']:
        repo = 'deephdc'

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

    # Add available Docker tags
    tags = retrieve_docker_tags(image=image, repo=repo)
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
Modules.get_items  = get_items
Modules.get_config = get_config
Modules.get_metadata = get_metadata  # TODO: inherit the common one, because it's the same for modules and tools


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
