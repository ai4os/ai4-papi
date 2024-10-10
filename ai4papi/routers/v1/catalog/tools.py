from copy import deepcopy
import json
import subprocess
import types
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.security import HTTPBearer

from ai4papi import quotas
import ai4papi.conf as papiconf
from ai4papi.routers.v1 import secrets as ai4secrets
from .common import Catalog, retrieve_docker_tags


security = HTTPBearer()


def get_config(
    self,
    item_name: str,
    vo: str,
    additional_info: Union[dict, None] = Body(default=None),
    authorization=Depends(security),
    ):
    """
    Returns the default configuration (dict) for creating a deployment
    for a specific item. It is prefilled with the appropriate
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

    # Retrieve tool metadata
    metadata = self.get_metadata(item_name)

    # Parse docker registry
    registry = metadata['sources']['docker_registry_repo']
    repo, image = registry.split('/')[:2]
    if repo not in ['deephdc', 'ai4oshub']:
        repo = 'ai4oshub'

    # Fill with correct Docker image and tags (not needed for CVAT because hardcoded)
    if item_name == 'ai4os-federated-server':
        conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

        tags = retrieve_docker_tags(image=image, repo=repo)
        conf["general"]["docker_tag"]["options"] = tags
        conf["general"]["docker_tag"]["value"] = tags[0]


    if item_name == 'ai4os-cvat' and additional_info:
        # Retrieve storage credentials
        storage_name = additional_info.get('storage_name')
        if storage_name:
            # Retrieve the rclone credentials
            secrets = ai4secrets.get_secrets(
                vo=vo,
                subpath='/services/storage/',
                authorization=types.SimpleNamespace(
                    credentials=authorization.credentials,
                ),
            )
            storage = secrets[f'/services/storage/{storage_name}']
            if not storage:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid storage name.",
                )

            # Use rclone to parse the existing CVAT backups to restore from
            result = subprocess.run([
                f"export RCLONE_CONFIG_RSHARE_VENDOR={storage['vendor']} && "
                f"export RCLONE_CONFIG_RSHARE_URL={storage['server']}/remote.php/webdav/ && "
                "export RCLONE_CONFIG_RSHARE_TYPE=webdav && "
                f"export RCLONE_CONFIG_RSHARE_USER={storage['loginName']} && "
                f"export RCLONE_CONFIG_RSHARE_PASS={storage['appPassword']} && "
                "export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $RCLONE_CONFIG_RSHARE_PASS) && "
                "rclone lsjson rshare:/ai4os-storage/tools/cvat/backups ;"
                "for var in $(env | grep '^RCLONE_CONFIG_RSHARE_' | awk -F= '{print $1}'); do unset $var; done"
                ],
                shell=True,
                capture_output=True,
                text=True
            )

            # Parse the JSON output
            try:
                json_output = json.loads(result.stdout)
                backups = []
                for path in json_output:
                    if path['IsDir']:
                        backups.append(path['Path'])
                conf["general"]["cvat_backup"]["options"] += backups
            except Exception:
                print('CVAT: No backups found')
                pass

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
