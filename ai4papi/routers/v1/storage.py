"""
Misc utilities regarding AI4OS compatible storages.
"""

import json
import os
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


def run_clone(command, storage_name, vo, token):
    """
    Run an RCLONE command, setting the appropriate configuration based on the user
    secrets stored in Vault.
    """
    # Retrieve the rclone credentials
    secrets = ai4secrets.get_secrets(
        vo=vo,
        subpath="/services/storage/",
        authorization=types.SimpleNamespace(
            credentials=token,
        ),
    )
    storage = secrets[f"/services/storage/{storage_name}"]
    if not storage:
        raise HTTPException(
            status_code=401,
            detail="Invalid storage name.",
        )

    # Use rclone to delete the subpath
    result = subprocess.run(
        [
            f"export RCLONE_CONFIG_RSHARE_VENDOR={storage['vendor']} && "
            f"export RCLONE_CONFIG_RSHARE_URL={storage['server']}/remote.php/dav/files/{storage['loginName']} && "
            "export RCLONE_CONFIG_RSHARE_TYPE=webdav && "
            f"export RCLONE_CONFIG_RSHARE_USER={storage['loginName']} && "
            f"export RCLONE_CONFIG_RSHARE_PASS={storage['appPassword']} && "
            "export RCLONE_CONFIG_RSHARE_PASS=$(rclone obscure $RCLONE_CONFIG_RSHARE_PASS) && "
            f"{command} ;"
            "status=$? ;"  # we want to return the status code of the RCLONE command
            "for var in $(env | grep '^RCLONE_CONFIG_RSHARE_' | awk -F= '{print $1}'); do unset $var; done;"
            "exit $status"
        ],
        shell=True,
        capture_output=True,
        text=True,
    )

    # Check for possible errors
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Error running the RCLONE command. \n\n {result.stderr}",
        )

    return result


@router.get("/{storage_name}/ls")
def storage_ls(
    vo: str,
    storage_name: str,
    subpath: str = "",
    authorization=Depends(security),
):
    """
    Returns a list of files/folders inside a given subpath of the specified storage.
    It is using RCLONE under-the-hood.

    It is used for example to allow listing CVAT snapshots from storage.

    Parameters:
    * **vo**: Virtual Organization where you want to create your deployment
    * **storage_name**: storage to parse.
    * **subpath**: subpath to query
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    if storage_name:
        # Sanitize user path (avoid security risk)
        subpath = subpath.strip().split(" ")[0]
        subpath = os.path.normpath(subpath).strip()

        # Run RCLONE command
        result = run_clone(
            command=f"rclone lsjson rshare:/{subpath}",
            storage_name=storage_name,
            vo=vo,
            token=authorization.credentials,
        )

        # Parse the JSON output
        try:
            json_output = json.loads(result.stdout)
            return json_output
        except Exception:
            raise HTTPException(
                status_code=500,
                detail=f"Error retrieving information from storage. \n\n {result.stderr}",
            )


@router.delete("/{storage_name}/rm")
def storage_rm(
    vo: str,
    storage_name: str,
    subpath: str,
    authorization=Depends(security),
):
    """
    Deletes the files/folders inside a given subpath of the specified storage.
    It is using RCLONE under-the-hood.

    It is used for example to allow deleting CVAT snapshots from storage.

    Parameters:
    * **vo**: Virtual Organization where you want to create your deployment
    * **storage_name**: storage to parse.
    * **subpath**: subpath of the file/folder to delete
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Do not allow to delete root folder to prevent accidents
    if not subpath.strip("/"):
        raise HTTPException(
            status_code=400,
            detail="You cannot delete the root folder for security reasons.",
        )

    if storage_name:
        # Sanitize user path (avoid security risk)
        subpath = subpath.strip().split(" ")[0]
        subpath = os.path.normpath(subpath).strip()

        # Run RCLONE command
        _ = run_clone(
            command=f"rclone purge rshare:/{subpath}",
            storage_name=storage_name,
            vo=vo,
            token=authorization.credentials,
        )

        return {"status": "success"}
