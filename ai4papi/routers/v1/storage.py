"""
Misc utilities regarding AI4OS compatible storages.
"""

import json
import subprocess
import types

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth
from ai4papi.routers.v1 import secrets as ai4secrets


router = APIRouter(
    prefix="/storage",
    tags=["Storage utilities"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


@router.get("/{storage_name}/ls")
def storage_ls(
    vo: str,
    storage_name: str,
    subpath: str = '',
    authorization=Depends(security),
    ):
    """
    Returns a list of files/folders inside a given subpath of the specified storage.
    It is using RCLONE under-the-hood.

    Parameters:
    * **vo**: Virtual Organization where you want to create your deployment
    * **storage_name**: storage to parse.
    * **subpath**: subpath to query
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Retrieve storage credentials
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
            f"export RCLONE_CONFIG_RSHARE_URL={storage['server']}/remote.php/dav/files/{storage['loginName']} && "
            "export RCLONE_CONFIG_RSHARE_TYPE=webdav && "
            f"export RCLONE_CONFIG_RSHARE_USER={storage['loginName']} && "
            f"export RCLONE_CONFIG_RSHARE_PASS={storage['appPassword']} && "
            "export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $RCLONE_CONFIG_RSHARE_PASS) && "
            f"rclone lsjson rshare:/{subpath} ;"
            "for var in $(env | grep '^RCLONE_CONFIG_RSHARE_' | awk -F= '{print $1}'); do unset $var; done"
            ],
            shell=True,
            capture_output=True,
            text=True
        )

        # Parse the JSON output
        try:
            json_output = json.loads(result.stdout)
            return json_output
        except Exception:
            raise HTTPException(
                status_code=500,
                detail=f"Error retrieving information from storage. \n \n {result.stderr}",
            )
