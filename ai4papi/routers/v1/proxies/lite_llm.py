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


@router.get("/api_keys")
def get_api_keys(authorization=Depends(security)):
    """
    Retrieve existing credentials for a user in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)

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
            }
            for item in data["keys"]
        ]
    else:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))

    return result


@router.post("/api_keys")
def create_api_key(key_name: str, authorization=Depends(security)):
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
