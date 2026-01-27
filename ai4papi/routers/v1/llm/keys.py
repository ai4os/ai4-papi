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


def update_teams(user_id: str, teams_to_add: list, teams_to_remove: list):
    """
    Update user teams in LiteLLM based on the current available roles for the
    authenticated user.
    """
    for team in teams_to_add:
        data = {"team_id": team, "member": {"user_id": user_id, "role": "user"}}
        session.post(f"{LITELLM_URL}/team/member_add", json=data)

    for team in teams_to_remove:
        data = {"team_id": team, "user_id": user_id}
        session.post(f"{LITELLM_URL}/team/member_delete", json=data)


def get_user(user_id: str):
    """
    Get user from LiteLLM based on the current authenticated user.
    Returns None if user does not exist.
    """
    r = session.get(f"{LITELLM_URL}/user/info", params={"user_id": user_id})
    user = r.json()
    if "user_id" in user["user_info"]:
        return user
    else:  # The user does not exist
        return None


def create_user(user_id: str, user_email: str, teams: list):
    """
    Create user inside LiteLLM based on the current authenticated user.
    """
    data = {"user_id": user_id, "user_email": user_email, "teams": teams}
    session.post(f"{LITELLM_URL}/user/new", json=data)


@router.get("")
def get_api_keys(authorization=Depends(security)):
    """
    Retrieve existing credentials for a user in LiteLLM.
    We also check that the user exists and belongs to the correct teams in LiteLLM.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    user_id = auth_info["id"]
    user_email = auth_info.get("email")
    current_teams = list(auth_info["groups"].keys())

    # Check if user exists in LiteLLM, if not create it
    user = get_user(user_id)
    if user is None:
        create_user(user_id, user_email, current_teams)
    else:
        # Check and update user teams
        old_teams = [team["team_id"] for team in user["teams"]]
        old_set = set(old_teams)
        current_set = set(current_teams)

        teams_to_add = list(current_set - old_set)
        teams_to_remove = list(old_set - current_set)
        update_teams(user_id, teams_to_add, teams_to_remove)

    # Retrieve the API keys
    r = session.get(
        f"{LITELLM_URL}/key/list",
        params={"user_id": {auth_info["id"]}, "return_full_object": "true"},
    )

    result = [
        {
            "id": item["key_alias"].split("_")[-1],
            "created_at": item["created_at"],
            "team_id": item["team_id"],
            "expires": item["expires"],
        }
        for item in r.json()["keys"]
    ]
    return result


@router.post("")
def create_api_key(
    key_name: str,
    team_id: str,
    duration: str = None,
    authorization=Depends(security),
):
    """
    Create a new API key in LiteLLM.

    Parameters
    ----------
    * team_id: str
      Team to which the key will be associated
    * duration: str
      Expiration date of the key (e.g. "30d", "1y").
      If left empty, the key does not expire
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    id = auth_info["id"]

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
