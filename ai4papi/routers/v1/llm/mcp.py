"""Manage external MCP servers exposed through LiteLLM."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
import requests

from ai4papi import auth
import ai4papi.conf as papiconf
from ai4papi.litellm_client import LiteLLMClient
from ai4papi.mcp_registry import (
    MCPRegistryClient,
    MCPRegistryError,
    MCPRegistryTimeoutError,
    MCPRegistryUnavailableError,
    MCPServerNotFoundError,
    select_remote_endpoint,
)

router = APIRouter(
    prefix="/mcp",
    tags=["AI4OS LLM (MCP)"],
    responses={404: {"description": "Not found"}},
)

security = HTTPBearer()


class MCPCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        description="Exact name in the official MCP registry",
    )


def get_registry_client() -> MCPRegistryClient:
    return MCPRegistryClient()


def get_litellm_client() -> LiteLLMClient:
    return LiteLLMClient(
        base_url=papiconf.LITELLM_URL,
        api_key=papiconf.LITELLM_API_KEY,
        timeout=papiconf.LITELLM_TIMEOUT,
    )


@router.get("")
def list_mcps(
    authorization=Depends(security),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    """List only the MCP servers owned by the authenticated user."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    try:
        servers = litellm.list_user_accessible_mcp_servers(auth_info["id"])
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return servers


@router.post("", status_code=201)
def create_remote_mcp(
    request: MCPCreateRequest,
    authorization=Depends(security),
    registry: MCPRegistryClient = Depends(get_registry_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    """Register the latest active remote endpoint of an official-registry MCP."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    try:
        # Registry lookup and endpoint selection are read-only. LiteLLM is mutated
        # only after the requested name resolves to a usable remote endpoint.
        registry_server = registry.get_server(request.name)
        endpoint = select_remote_endpoint(registry_server)
        registration = litellm.create_remote_mcp_server(
            registry_server=registry_server,
            owner=auth_info["id"],
            endpoint=endpoint,
        )
        server_id = registration["server_id"]
        access_group_id = None
        try:
            # keys that already exist: assign a private access group
            # directly to them. This grant does not modify or broaden a shared
            # authorization-level team such as ``ap-d``.
            key_ids = litellm.list_user_mcp_key_ids(auth_info["id"])
            access_group = litellm.create_mcp_access_group(
                owner=auth_info["id"],
                server_id=server_id,
                key_ids=key_ids,
            )
            access_group_id = access_group["access_group_id"]

            # Save the relation in MCP metadata. Besides making the ownership and
            # cleanup policy auditable, keys created later can discover this group
            # and receive the same private grant during /key/generate.
            mcp_info = dict(registration.get("mcp_info") or {})
            mcp_info.update(
                {
                    "access_group_id": access_group_id,
                    "access_group_name": access_group["access_group_name"],
                }
            )
            registration = litellm.update_mcp_server_metadata(
                server_id,
                mcp_info,
            )
        except Exception:
            # Creation is a three-step operation in LiteLLM. If a later step
            # fails, remove any access group and MCP already created so that a
            # retry starts from a clean state.
            if access_group_id is not None:
                try:
                    litellm.delete_mcp_access_group(
                        access_group_id,
                        ignore_not_found=True,
                    )
                except requests.RequestException:
                    pass
            try:
                litellm.delete_mcp_server(server_id)
            except requests.RequestException:
                pass
            raise

    except MCPServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MCPRegistryTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except MCPRegistryUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MCPRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "name": registry_server["name"],
        "version": registry_server["version"],
        "deployment_type": "remote",
        "endpoint": endpoint,
        "registration": registration,
    }


@router.delete("/{server_id}", status_code=204)
def delete_mcp(
    server_id: str,
    authorization=Depends(security),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    """Delete an MCP server after verifying ownership in LiteLLM metadata."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    try:
        server = litellm.get_mcp_server(server_id)
        mcp_info = server.get("mcp_info") or {}
        owner = mcp_info.get("owner")

        # The admin key used internally could delete any server. Enforce ownership
        # in PAPI before making that LiteLLM request.
        if owner != auth_info["id"]:
            raise HTTPException(
                status_code=403,
                detail="You are not the owner of that MCP server.",
            )

        # Delete the private group first. LiteLLM then removes its ID from every
        # assigned key atomically. A missing group is harmless on a retried delete.
        access_group_id = mcp_info.get("access_group_id")
        if access_group_id:
            litellm.delete_mcp_access_group(
                access_group_id,
                ignore_not_found=True,
            )

        litellm.delete_mcp_server(server_id)

    except HTTPException:
        raise
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
