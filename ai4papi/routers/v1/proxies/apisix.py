"""
Proxy to manage APISIX routes.

This router exposes simplified endpoints for the dashboard to interact
with APISIX Admin API. The request to our API is authenticated with
a Bearer token. The internal call to APISIX uses the APISIX admin API token.
"""


import os
import requests
import secrets
import json
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer

from ai4papi import auth

import ai4papi.conf as papiconf

router = APIRouter(
    prefix="/apisix",
    tags=["Proxies (APISIX)"],
    responses={404: {"description": "Not found"}},
)

security = HTTPBearer()

# APISIX Admin API configuration
APISIX_URL = os.environ.get("APISIX_URL")
if not APISIX_URL:
    if papiconf.IS_DEV:  # Not enforced for developers
        print('"APISIX_URL" envar is not defined')
    else:
        raise Exception('You need to define the variable "APISIX_URL".')

APISIX_API_KEY = os.environ.get("APISIX_API_KEY")
if not APISIX_API_KEY:
    if papiconf.IS_DEV:  # Not enforced for developers
        print('"APISIX_API_KEY" envar is not defined')
    else:
        raise Exception('You need to define the variable "APISIX_API_KEY".')
        
# Session used only to call APISIX
session = requests.Session()
session.headers.update({
    "X-API-KEY": APISIX_API_KEY,
    "Content-Type": "application/json"
})


def list_consumers():
    """
    List all consumers in APISIX.
    """
    resp = session.get(f"{APISIX_URL}/consumers")
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))
    
    data = resp.json()
    usernames = [item["value"]["username"] for item in data.get("list", [])]
    return usernames


def add_credential(user_id: str, name: str):
    """
    Add new credential to consumer in APISIX.
    """
    token = secrets.token_urlsafe(32)
    credential_data = {
        "plugins": {
            "key-auth": {
                "key": token
            }
        }
    }
    
    resp = session.put(
        f"{APISIX_URL}/consumers/{user_id}/credentials/{name}",
        json=credential_data
    )
    
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))
    
    return token


@router.get("/api_keys")
def get_api_keys(
    authorization=Depends(security)
):
    """
    Retrieve existing credentials for a consumer in APISIX.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
        
    resp = session.get(
        f"{APISIX_URL}/consumers/{auth_info["id"]}/credentials")
    
    if resp.status_code == 404:
        result = []
    elif not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))
    else: 
        credentials = json.loads(resp.text)
        result = [
        {
            "id": item["value"]["id"],
            "api_key": item["value"]["plugins"]["key-auth"]["key"]
        }
            for item in credentials["list"]
        ]

    return result


@router.post("/api_keys")
def create_api_key(
    name: str,
    authorization=Depends(security)
):
    """
    Create a new credential in APISIX.

    If consumer exists, adds the new credential using credentials endpoint. 
    If consumer does not exist, creates the consumer using the consumers endpoint
    and then their first credential using the credentials endpoint [1].
    
    **Notes**:
    [1]: https://apisix.apache.org/docs/apisix/terminology/credential/#example
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    
    consumers = list_consumers()
    
    if auth_info['id'] not in consumers:
        print(f"Consumer '{auth_info['id']}' does not exist. Creating consumer.")
        consumer_data = {
            "username": auth_info['id']
        }
        
        resp = session.put(
            f"{APISIX_URL}/consumers",
            json=consumer_data
        )
        
        if not resp.ok:
            raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))
        
    token = add_credential(auth_info["id"], name)
    
    return {token}


@router.delete("/api_keys")
def delete_api_key(
    name: str,
    authorization=Depends(security)
):
    """
    Delete a credential in APISIX.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
  
    resp = session.delete(
        f"{APISIX_URL}/consumers/{auth_info['id']}/credentials/{name}")
        
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=json.loads(resp.text))
        
    
    return {"status": "success"}