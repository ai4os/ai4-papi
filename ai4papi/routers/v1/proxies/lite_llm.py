"""
Proxy to manage LiteLLM routes.

This router exposes simplified endpoints for the dashboard to interact
with LiteLLM API. The request to our API is authenticated with
a Bearer token. The internal call to LiteLLM uses the LiteLLM admin API token.
"""

import os
import requests
import json
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer

from ai4papi import auth

import ai4papi.conf as papiconf

router = APIRouter(
    prefix="/lite_llm",
    tags=["Proxies (LiteLLM)"],
    responses={404: {"description": "Not found"}},
)

security = HTTPBearer()

# LiteLLM API configuration
LITELLM_URL = os.environ.get("LITELLM_URL")
if not LITELLM_URL:
    if papiconf.IS_DEV:  # Not enforced for developers
        print('"LITELLM_URL" envar is not defined')
    else:
        raise Exception('You need to define the variable "LITELLM_URL".')

LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY")
if not LITELLM_API_KEY:
    if papiconf.IS_DEV:  # Not enforced for developers
        print('"LITELLM_API_KEY" envar is not defined')
    else:
        raise Exception('You need to define the variable "LITELLM_API_KEY".')

# Session used only to call LITELLM
session = requests.Session()
session.headers.update(
    {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}
)


def update_teams(user_id, teams_to_add, teams_to_remove):
    """
    Update user teams in LiteLLM based on the current authenticated user.
    Adds and removes teams as necessary.
    """
    for team in teams_to_add:
        data = {"team_id": team, "member": {"user_id": user_id, "role": "user"}}
        resp_add = session.post(
            f"{LITELLM_URL}/team/member_add",
            json=data,
        )

        if resp_add.ok == False:
            raise HTTPException(
                status_code=resp_add.status_code, detail=json.loads(resp_add.text)
            )

    for team in teams_to_remove:
        data = {"team_id": team, "user_id": user_id}
        resp_delete = session.post(
            f"{LITELLM_URL}/team/member_delete",
            json=data,
        )

        if resp_delete.ok == False:
            raise HTTPException(
                status_code=resp_delete.status_code, detail=json.loads(resp_delete.text)
            )


def get_user(user_id):
    """
    Get user from LiteLLM based on the current authenticated user.
    """
    resp = session.get(
        f"{LITELLM_URL}/user/list",
    )

    if resp.ok:
        data = json.loads(resp.text)
        user = next(
            (u for u in data.get("users", []) if u.get("user_id") == user_id), None
        )
        return user
    else:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))


def create_user(user_id, user_email, teams):
    """
    Create user inside LiteLLM based on the current authenticated user.
    """
    data = {"user_id": user_id, "user_email": user_email, "teams": teams}
    resp_create = session.post(
        f"{LITELLM_URL}/user/new",
        json=data,
    )

    if resp_create.ok:
        return
    else:
        raise HTTPException(
            status_code=resp_create.status_code, detail=json.loads(resp_create.text)
        )


@router.get("/api_keys")
def get_api_keys(authorization=Depends(security)):
    """
    Retrieve existing credentials for a user in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    user_id = auth_info["id"]
    user_email = auth_info.get("email")
    current_teams = list(auth_info["groups"].keys())

    # Check if user exists in LiteLLM, if not create it
    user = get_user(user_id)
    if user == None:
        create_user(user_id, user_email, current_teams)
    else:
        # Check and update user teams
        old_teams = user.get("teams", [])
        old_set = set(old_teams)
        current_set = set(current_teams)

        teams_to_add = list(current_set - old_set)
        teams_to_remove = list(old_set - current_set)
        update_teams(user_id, teams_to_add, teams_to_remove)

    resp = session.get(
        f"{LITELLM_URL}/key/list",
        params={"user_id": {auth_info["id"]}, "return_full_object": "true"},
    )

    if resp.ok:
        data = json.loads(resp.text)
        result = [
            {
                "id": item["key_alias"].split("_")[-1],
                "created_at": item["created_at"],
                "team_id": item["team_id"],
            }
            for item in data["keys"]
        ]
    else:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))

    return result


@router.post("/api_keys")
def create_api_key(key_name: str, team_id: str, authorization=Depends(security)):
    """
    Create a new credential in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    id = auth_info["id"]

    data = {
        "user_id": id,
        "key_alias": f"{id}_{key_name}",
        "key_type": "llm_api",
        "team_id": team_id,
    }
    resp = session.post(f"{LITELLM_URL}/key/generate", json=data)

    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))

    data = json.loads(resp.text)

    return data["key"]


@router.delete("/api_keys")
def delete_api_key(key_name: str, authorization=Depends(security)):
    """
    Delete a credential in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    id = auth_info["id"]

    data = {"key_aliases": [f"{id}_{key_name}"]}
    resp = session.post(f"{LITELLM_URL}/key/delete", json=data)

    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))

    return {"status": "success"}
