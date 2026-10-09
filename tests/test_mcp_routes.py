from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import requests
from fastapi import HTTPException

from ai4papi.routers.v1.llm import mcp


def _authenticate_user(monkeypatch):
    """Make a route call represent one authorized ai4eosc user."""

    monkeypatch.setattr(
        mcp.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-u": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(
        mcp.auth,
        "check_authorization",
        lambda auth_info, vo=None: None,
    )


def test_list_mcps_combines_litellm_servers_and_nomad_deployments(monkeypatch):
    _authenticate_user(monkeypatch)

    # LiteLLM has already applied owner, public, key and team grants. This test
    # uses an owned remote registration and one self-deployed registration.
    litellm = Mock()
    litellm.list_user_accessible_mcp_servers.return_value = [
        {
            "server_id": "remote-id",
            "url": "https://weather.example/mcp",
            "transport": "http",
            "mcp_info": {
                "owner": "user-id",
                "registry_name": "com.example/weather",
                "registry_version": "1.0.0",
                "deployment_type": "remote",
            },
        },
        {
            "server_id": "deployed-id",
            "url": "https://mcp-job.node.example/mcp",
            "transport": "http",
            "static_headers": {"Authorization": "Basic secret"},
            "mcp_info": {
                "owner": "user-id",
                "registry_name": "com.example/local",
                "registry_version": "2.0.0",
                "deployment_type": "self_deployed",
                "nomad_job_id": "mcp-job",
                "nomad_namespace": "ai4eosc",
            },
        },
    ]

    # Nomad is queried only for the job referenced by the LiteLLM registration.
    nomad = Mock()
    nomad.get_mcp_deployment.return_value = {
        "job_id": "mcp-job",
        "namespace": "ai4eosc",
        "status": "running",
        "healthy": True,
        "endpoint": "https://mcp-job.node.example/mcp",
    }

    result = mcp.list_mcps(
        authorization=SimpleNamespace(credentials="token"),
        litellm=litellm,
        nomad=nomad,
    )

    # Remote MCPs have no deployment. Self-deployed MCPs contain both sides of the
    # lifecycle, but their Basic credential is never exposed by PAPI's list route.
    assert result[0]["deployment"] is None
    assert result[1]["deployment"]["status"] == "running"
    assert result[1]["registration"]["static_headers"] == {"Authorization": "***"}

    # The route does not enumerate Nomad jobs. LiteLLM remains the source of truth,
    # and the remote MCP causes no Nomad request at all.
    litellm.list_user_accessible_mcp_servers.assert_called_once_with("user-id")
    assert nomad.method_calls == [call.get_mcp_deployment("mcp-job", "ai4eosc")]


def test_create_remote_mcp_creates_private_access_group(monkeypatch):
    _authenticate_user(monkeypatch)

    # Registry case: a provider-hosted endpoint exists, so PAPI prefers it and does
    # not consume Nomad resources even if the entry were hybrid.
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/weather",
        "version": "1.0.0",
        "remotes": [{"type": "streamable-http", "url": "https://weather.example/mcp"}],
    }
    nomad = Mock()

    # LiteLLM creates the private server and adds it to the one stable access group
    # shared by all MCPs and keys belonging to this user.
    litellm = Mock()
    litellm.create_remote_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "user-id"},
    }
    litellm.list_user_mcp_key_ids.return_value = ["hashed-key"]
    litellm.add_mcp_server_to_user_access_group.return_value = {
        "access_group_id": "group-id",
        "access_group_name": "papi_user_mcp_group",
    }
    litellm.update_mcp_server_metadata.return_value = {
        "server_id": "server-id",
        "mcp_info": {
            "owner": "user-id",
            "access_group_id": "group-id",
            "access_group_name": "papi_user_mcp_group",
        },
    }

    result = mcp.create_mcp(
        mcp.MCPCreateRequest(name="com.example/weather"),
        authorization=SimpleNamespace(credentials="token"),
        registry=registry,
        litellm=litellm,
        nomad=nomad,
    )

    assert result["deployment_type"] == "remote"
    assert result["deployment"] is None
    assert result["registration"]["mcp_info"]["access_group_id"] == "group-id"

    # Critical privacy case: the router passes only the owner's keys. No shared VO
    # team receives this group, so another member of ap-d cannot inherit the MCP.
    litellm.add_mcp_server_to_user_access_group.assert_called_once_with(
        owner="user-id",
        server_id="server-id",
        key_ids=["hashed-key"],
    )
    nomad.create_mcp_deployment.assert_not_called()


def test_create_self_deployed_mcp_submits_self_registering_nomad_job(monkeypatch):
    _authenticate_user(monkeypatch)

    # Package-only Registry case: this npm process serves Streamable HTTP itself,
    # so Nomad can run it directly without a stdio adapter or Supergateway.
    package = {
        "registryType": "npm",
        "identifier": "@example/weather-mcp",
        "version": "2.0.0",
        "transport": {
            "type": "streamable-http",
            "url": "http://localhost:3001/mcp",
        },
    }
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/weather",
        "version": "2.0.0",
        "packages": [package],
    }

    # PAPI receives only the submitted job state. Registration and readiness are
    # handled later by lifecycle tasks inside the Nomad allocation.
    nomad = Mock()
    nomad.create_mcp_deployment.return_value = {
        "job_id": "mcp-job",
        "namespace": "ai4eosc",
        "status": "submitted",
        "endpoint": None,
    }

    # This client must remain untouched: the job's poststart task owns LiteLLM
    # registration, metadata and access-group synchronization.
    litellm = Mock()

    result = mcp.create_mcp(
        mcp.MCPCreateRequest(
            name="com.example/weather",
            vo="vo.ai4eosc.eu",
            conf={"hardware": {"cpu_num": 2, "ram": 2048, "disk": 4096}},
        ),
        authorization=SimpleNamespace(credentials="token"),
        registry=registry,
        litellm=litellm,
        nomad=nomad,
    )

    assert result["deployment_type"] == "self_deployed"
    assert result["deployment"]["status"] == "submitted"
    assert result["endpoint"] is None
    assert result["registration"] is None
    nomad.create_mcp_deployment.assert_called_once_with(
        registry_server=registry.get_server.return_value,
        package=package,
        owner="user-id",
        namespace="ai4eosc",
        base_domain="deployments.cloud.ai4eosc.eu",
        litellm_url=mcp.papiconf.LITELLM_URL,
        litellm_api_key=mcp.papiconf.LITELLM_API_KEY,
        resources={"cpu_num": 2, "ram": 2048, "disk": 4096},
    )
    litellm.create_remote_mcp_server.assert_not_called()
    litellm.add_mcp_server_to_user_access_group.assert_not_called()
    litellm.update_mcp_server_metadata.assert_not_called()


def test_create_self_deployed_mcp_accepts_stdio_and_submits_nomad(monkeypatch):
    _authenticate_user(monkeypatch)
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/stdio-only",
        "version": "1.0.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/stdio-only",
                "version": "1.0.0",
                "transport": {"type": "stdio"},
            }
        ],
    }
    nomad = Mock()
    nomad.create_mcp_deployment.return_value = {
        "job_id": "mcp-stdio-job",
        "namespace": "ai4eosc",
        "status": "submitted",
        "endpoint": None,
    }

    # The route treats stdio as deployable. MCPNomadClient is responsible for
    # placing Supergateway in front and exposing a uniform Streamable HTTP URL.
    result = mcp.create_mcp(
        mcp.MCPCreateRequest(name="com.example/stdio-only"),
        authorization=SimpleNamespace(credentials="token"),
        registry=registry,
        litellm=Mock(),
        nomad=nomad,
    )

    assert result["deployment_type"] == "self_deployed"
    assert result["deployment"]["job_id"] == "mcp-stdio-job"
    assert (
        nomad.create_mcp_deployment.call_args.kwargs["package"]["transport"]["type"]
        == "stdio"
    )
    assert nomad.create_mcp_deployment.call_args.kwargs["resources"] == {
        "cpu_num": 1,
        "ram": 1024,
        "disk": 2048,
    }


def test_create_self_deployed_mcp_rejects_resources_outside_mcp_limits(monkeypatch):
    _authenticate_user(monkeypatch)
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/stdio-only",
        "version": "1.0.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/stdio-only",
                "version": "1.0.0",
                "transport": {"type": "stdio"},
            }
        ],
    }
    nomad = Mock()

    # Resource validation follows the catalog-module flow and happens before the
    # job submission, so an excessive request cannot consume cluster resources.
    with pytest.raises(HTTPException) as exc:
        mcp.create_mcp(
            mcp.MCPCreateRequest(
                name="com.example/stdio-only",
                conf={"hardware": {"cpu_num": 11}},
            ),
            authorization=SimpleNamespace(credentials="token"),
            registry=registry,
            litellm=Mock(),
            nomad=nomad,
        )

    assert exc.value.status_code == 400
    assert "cpu_num" in exc.value.detail
    nomad.create_mcp_deployment.assert_not_called()


def test_create_self_deployed_mcp_still_rejects_sse_package(monkeypatch):
    _authenticate_user(monkeypatch)
    registry = Mock()
    registry.get_server.return_value = {
        "name": "com.example/sse-only",
        "version": "1.0.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/sse-only",
                "version": "1.0.0",
                "transport": {"type": "sse"},
            }
        ],
    }
    nomad = Mock()

    # SSE remains outside this increment: only native Streamable HTTP and stdio
    # adapted to Streamable HTTP can currently be submitted to Nomad.
    with pytest.raises(HTTPException) as exc:
        mcp.create_mcp(
            mcp.MCPCreateRequest(name="com.example/sse-only"),
            authorization=SimpleNamespace(credentials="token"),
            registry=registry,
            litellm=Mock(),
            nomad=nomad,
        )

    assert exc.value.status_code == 422
    assert "SSE is not supported yet" in exc.value.detail
    nomad.create_mcp_deployment.assert_not_called()


def test_create_remote_mcp_rolls_back_grant_and_server(monkeypatch):
    _authenticate_user(monkeypatch)
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
    litellm.add_mcp_server_to_user_access_group.return_value = {
        "access_group_id": "group-id",
        "access_group_name": "papi_user_mcp_group",
    }

    # Failure case: the stable group was updated but the final metadata link fails.
    # Rollback removes only this MCP grant and preserves the user's group.
    response = requests.Response()
    response.status_code = 500
    response._content = b"metadata update failed"
    litellm.update_mcp_server_metadata.side_effect = requests.HTTPError(
        response=response
    )
    nomad = Mock()

    with pytest.raises(HTTPException) as exc:
        mcp.create_mcp(
            mcp.MCPCreateRequest(name="com.example/weather"),
            authorization=SimpleNamespace(credentials="token"),
            registry=registry,
            litellm=litellm,
            nomad=nomad,
        )

    assert exc.value.status_code == 500
    litellm.remove_mcp_server_from_user_access_group.assert_called_once_with(
        owner="user-id",
        server_id="server-id",
    )
    litellm.delete_mcp_server.assert_called_once_with("server-id")
    nomad.delete_mcp_deployment.assert_not_called()


def test_delete_mcp_checks_owner_before_touching_either_backend(monkeypatch):
    _authenticate_user(monkeypatch)
    litellm = Mock()
    litellm.get_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "another-user"},
    }
    nomad = Mock()

    # The internal LiteLLM key is administrative, so PAPI itself must enforce the
    # Keycloak owner before any destructive call to LiteLLM or Nomad.
    with pytest.raises(HTTPException) as exc:
        mcp.delete_mcp(
            "server-id",
            authorization=SimpleNamespace(credentials="token"),
            litellm=litellm,
            nomad=nomad,
        )

    assert exc.value.status_code == 403
    nomad.delete_mcp_deployment.assert_not_called()
    litellm.remove_mcp_server_from_user_access_group.assert_not_called()
    litellm.delete_mcp_server.assert_not_called()


def test_delete_remote_mcp_only_updates_stable_group_and_server(monkeypatch):
    _authenticate_user(monkeypatch)
    litellm = Mock()
    litellm.get_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {"owner": "user-id", "deployment_type": "remote"},
    }
    nomad = Mock()

    mcp.delete_mcp(
        "server-id",
        authorization=SimpleNamespace(credentials="token"),
        litellm=litellm,
        nomad=nomad,
    )

    # No migration or per-server-group cleanup exists. Remote deletion only
    # removes this ID from the user's reusable group and deletes the server.
    litellm.remove_mcp_server_from_user_access_group.assert_called_once_with(
        owner="user-id", server_id="server-id"
    )
    litellm.delete_mcp_server.assert_called_once_with("server-id")
    nomad.delete_mcp_deployment.assert_not_called()


def test_delete_self_deployed_mcp_stops_job_and_deregisters_litellm(monkeypatch):
    _authenticate_user(monkeypatch)
    litellm = Mock()
    litellm.get_mcp_server.return_value = {
        "server_id": "server-id",
        "mcp_info": {
            "owner": "user-id",
            "deployment_type": "self_deployed",
            "nomad_job_id": "mcp-job",
            "nomad_namespace": "ai4eosc",
            "access_group_id": "group-id",
        },
    }
    nomad = Mock()

    mcp.delete_mcp(
        "server-id",
        authorization=SimpleNamespace(credentials="token"),
        litellm=litellm,
        nomad=nomad,
    )

    # Stop Nomad first, then synchronously revoke the stable-group grant and remove
    # the registration. The job's poststop task remains an idempotent fallback.
    nomad.delete_mcp_deployment.assert_called_once_with(
        job_id="mcp-job",
        namespace="ai4eosc",
        owner="user-id",
        ignore_not_found=True,
    )
    litellm.remove_mcp_server_from_user_access_group.assert_called_once_with(
        owner="user-id", server_id="server-id"
    )
    litellm.delete_mcp_server.assert_called_once_with("server-id")
