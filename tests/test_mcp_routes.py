from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException

from ai4papi.routers.v1.llm import mcp


def test_list_mcps_returns_servers_accessible_to_user(monkeypatch):
    # Simulate the identity obtained from the caller's OIDC token. The router must
    # pass the Keycloak subject to the LiteLLM permission resolver.
    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(mcp.auth, "check_authorization", lambda auth_info: None)

    # The client has already combined ownership, public and team-based visibility;
    # this router test checks that PAPI returns that filtered result unchanged.
    litellm = Mock()
    litellm.list_user_accessible_mcp_servers.return_value = [
        {"server_id": "owned"},
        {"server_id": "public"},
        {"server_id": "team"},
    ]

    result = mcp.list_mcps(
        authorization=SimpleNamespace(credentials="token"),
        litellm=litellm,
    )

    # The result may legitimately contain MCPs not owned by the caller when they
    # are public or shared through one of the caller's teams.
    assert result == [
        {"server_id": "owned"},
        {"server_id": "public"},
        {"server_id": "team"},
    ]

    # Never ask for the unfiltered admin catalogue from this user-facing route.
    litellm.list_user_accessible_mcp_servers.assert_called_once_with("user-id")


def test_create_remote_mcp_creates_private_access_group(monkeypatch):
    # Authenticate the owner who is requesting the MCP registration.
    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(mcp.auth, "check_authorization", lambda auth_info: None)

    # Registry case: the requested entry exposes a ready-to-use Streamable HTTP
    # endpoint, so this first implementation can register it without Nomad.
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/weather",
        "version": "1.0.0",
        "remotes": [{"type": "streamable-http", "url": "https://weather.example/mcp"}],
    }

    # LiteLLM first creates the private server, then returns the owner's existing
    # key identifiers so PAPI can attach the new access group directly to them.
    litellm = Mock()
    litellm.create_remote_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "user-id"},
    }
    litellm.list_user_mcp_key_ids.return_value = ["hashed-key"]

    # The created group contains the server and becomes the auditable link used by
    # existing keys, future keys and eventual deletion.
    litellm.create_mcp_access_group.return_value = {
        "access_group_id": "group-id",
        "access_group_name": "papi_mcp_group",
    }
    litellm.update_mcp_server_metadata.return_value = {
        "server_id": "server-id",
        "mcp_info": {
            "owner": "user-id",
            "access_group_id": "group-id",
            "access_group_name": "papi_mcp_group",
        },
    }

    result = mcp.create_remote_mcp(
        mcp.MCPCreateRequest(name="com.example/weather"),
        authorization=SimpleNamespace(credentials="token"),
        registry=registry,
        litellm=litellm,
    )

    # The final registration returned by PAPI must contain the persisted group ID.
    assert result["registration"]["mcp_info"]["access_group_id"] == "group-id"

    # Critical privacy check: the router passes only the owner's keys. The client
    # payload separately guarantees assigned_team_ids=[] for shared teams like ap-d.
    litellm.create_mcp_access_group.assert_called_once_with(
        owner="user-id",
        server_id="server-id",
        key_ids=["hashed-key"],
    )

    # Store the relation in mcp_info so a key created later can discover this group.
    litellm.update_mcp_server_metadata.assert_called_once_with(
        "server-id",
        {
            "owner": "user-id",
            "access_group_id": "group-id",
            "access_group_name": "papi_mcp_group",
        },
    )


def test_create_remote_mcp_rolls_back_group_and_server(monkeypatch):
    # Use a valid authenticated owner and remote registry entry so execution reaches
    # the multi-step LiteLLM creation flow.
    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(mcp.auth, "check_authorization", lambda auth_info: None)
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/weather",
        "version": "1.0.0",
        "remotes": [{"type": "streamable-http", "url": "https://weather.example/mcp"}],
    }
    litellm = Mock()
    litellm.create_remote_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "user-id"},
    }
    litellm.list_user_mcp_key_ids.return_value = ["hashed-key"]
    litellm.create_mcp_access_group.return_value = {
        "access_group_id": "group-id",
        "access_group_name": "papi_mcp_group",
    }

    # Failure case: LiteLLM creates both resources but rejects the final metadata
    # update. Leaving either resource behind would produce an incomplete policy.
    response = requests.Response()
    response.status_code = 500
    response._content = b"metadata update failed"
    litellm.update_mcp_server_metadata.side_effect = requests.HTTPError(
        response=response
    )

    with pytest.raises(HTTPException) as exc:
        mcp.create_remote_mcp(
            mcp.MCPCreateRequest(name="com.example/weather"),
            authorization=SimpleNamespace(credentials="token"),
            registry=registry,
            litellm=litellm,
        )

    # Preserve LiteLLM's error status for the PAPI caller.
    assert exc.value.status_code == 500

    # Compensation must revoke the group first and then remove the orphaned server,
    # allowing a later retry to start without conflicting resources.
    litellm.delete_mcp_access_group.assert_called_once_with(
        "group-id",
        ignore_not_found=True,
    )
    litellm.delete_mcp_server.assert_called_once_with("server-id")


def test_delete_mcp_checks_owner(monkeypatch):
    # The authenticated caller is different from the owner stored in mcp_info.
    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(mcp.auth, "check_authorization", lambda auth_info: None)
    litellm = Mock()
    litellm.get_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "another-user"},
    }

    # Even though PAPI uses an administrative LiteLLM key internally, the router
    # must enforce ownership before issuing any destructive operation.
    with pytest.raises(HTTPException) as exc:
        mcp.delete_mcp(
            "server-id",
            authorization=SimpleNamespace(credentials="token"),
            litellm=litellm,
        )

    assert exc.value.status_code == 403

    # Negative authorization case: another user's MCP must remain untouched.
    litellm.delete_mcp_access_group.assert_not_called()
    litellm.delete_mcp_server.assert_not_called()


def test_delete_mcp_deletes_owned_server_and_private_access_group(monkeypatch):
    # Positive authorization case: mcp_info.owner matches the authenticated subject
    # and also contains the private group created during registration.
    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(mcp.auth, "check_authorization", lambda auth_info: None)
    litellm = Mock()
    litellm.get_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "user-id", "access_group_id": "group-id"},
    }

    mcp.delete_mcp(
        "server-id",
        authorization=SimpleNamespace(credentials="token"),
        litellm=litellm,
    )

    # Delete the access group so LiteLLM removes its reference from all owner keys.
    # A missing group is accepted to make a partially completed delete retryable.
    litellm.delete_mcp_access_group.assert_called_once_with(
        "group-id",
        ignore_not_found=True,
    )

    # Once access is revoked, remove the actual MCP server registration.
    litellm.delete_mcp_server.assert_called_once_with("server-id")
