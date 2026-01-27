"""
Manage LiteLLM API keys.

This router exposes simplified endpoints for the dashboard to interact
with LiteLLM API. The request to our API is authenticated with
a Bearer token. The internal call to LiteLLM uses the LiteLLM admin API token.
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer
import requests

from ai4papi import auth
import ai4papi.conf as papiconf


router = APIRouter(
    prefix="/api_keys",
    tags=["AI4OS LLM (keys)"],
    responses={404: {"description": "Not found"}},
)

security = HTTPBearer()

# LiteLLM API configuration
LITELLM_URL = "https://vllm.cloud.ai4eosc.eu"
LITELLM_API_KEY = papiconf.load_env("LITELLM_API_KEY")


class LiteLLMSession(requests.Session):
    """
    Session that automatically raises a FastAPI HTTPException for failed LiteLLM
    responses.
    """

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        if not response.ok:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response


session = LiteLLMSession()
session.headers.update(
    {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}
)


@router.get("")
def get_api_keys(authorization=Depends(security)):
    """
    Retrieve existing credentials for a user in LiteLLM.

    We also use this function as an LiteLLM init:
    * check that the user exists, otherwise creates it
    * check that the user belongs to the correct teams in LiteLLM, otherwise update the
      teams
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    user_id = auth_info["id"]
    user_email = auth_info.get("email")
    current_levels = list(auth_info["groups"].keys())
    top_level = auth.get_highest_level(current_levels)

    # Retrieve the LiteLLM user
    r = session.get(f"{LITELLM_URL}/user/info", params={"user_id": user_id})
    user = r.json()

    # If user does not exist, create it and return no keys
    if "user_id" not in user["user_info"]:
        data = {"user_id": user_id, "user_email": user_email, "teams": [top_level]}
        session.post(f"{LITELLM_URL}/user/new", json=data)
        return [{}]

    # Retrieve the API keys
    r = session.get(
        f"{LITELLM_URL}/key/list",
        params={"user_id": {auth_info["id"]}, "return_full_object": "true"},
    )
    keys = r.json()["keys"]

    # Check if user belongs to the team of their current top level
    if top_level not in [team["team_id"] for team in user["teams"]]:
        data = {"team_id": top_level, "member": {"user_id": user_id, "role": "user"}}
        session.post(f"{LITELLM_URL}/team/member_add", json=data)

    # Check that API keys indeed belong to that top level team. Otherwise we migrate
    # the API keys.
    for k in keys:
        if k["team_id"] != top_level:
            data = {"key": k["token"], "team_id": top_level}
            session.post(f"{LITELLM_URL}/key/update", json=data)

    # Remove user from all teams that are not his top level
    # ⚠️ Do not update teams before changing API key team, otherwise the API keys will
    # be erased.
    teams_to_remove = set([team["team_id"] for team in user["teams"]])
    teams_to_remove.discard(top_level)
    for team in teams_to_remove:
        data = {"team_id": team, "user_id": user_id}
        session.post(f"{LITELLM_URL}/team/member_delete", json=data)

    # Build final key dict
    out = [
        {
            "id": item["key_alias"].split("_")[-1],
            "created_at": item["created_at"],
            "team_id": top_level,
            "expires": item["expires"],
        }
        for item in keys
    ]

    return out


@router.post("")
def create_api_key(
    key_name: str,
    duration: str = None,
    authorization=Depends(security),
):
    """
    Create a new API key in LiteLLM.

    Parameters
    ----------
    * duration: str
      Expiration date of the key (e.g. "30d", "1y").
      If left empty, the key does not expire
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    id = auth_info["id"]

    # Get user current top access level
    current_levels = list(auth_info["groups"].keys())
    team_id = auth.get_highest_level(current_levels)

    # Keys alias are created with pattern "<user_id>_<keyname>" as they must be globally unique.
    data = {
        "user_id": id,
        "key_alias": f"{id}_{key_name}",
        "key_type": "llm_api",
        "team_id": team_id,
        "duration": duration,
    }
    r = session.post(f"{LITELLM_URL}/key/generate", json=data)
    return r.json()["key"]


@router.delete("")
def delete_api_key(key_name: str, authorization=Depends(security)):
    """
    Delete a credential in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    id = auth_info["id"]

    data = {"key_aliases": [f"{id}_{key_name}"]}
    session.post(f"{LITELLM_URL}/key/delete", json=data)
    return {"status": "success"}
