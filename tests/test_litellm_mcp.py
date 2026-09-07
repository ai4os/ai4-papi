from unittest.mock import Mock

import requests

from ai4papi.litellm_client import (
    LiteLLMClient,
    build_litellm_mcp_access_group_name,
    build_litellm_mcp_server_name,
)


def test_mcp_server_name_is_stable_per_owner_and_registry_name():
    # Case 1 — same owner and registry entry: retries must generate exactly the
    # same LiteLLM name, otherwise one request could leave duplicate servers.
    first = build_litellm_mcp_server_name("com.example/weather.mcp", "user-1")
    second = build_litellm_mcp_server_name("com.example/weather.mcp", "user-1")

    # Case 2 — same registry entry but another owner: each user needs a distinct
    # registration because ownership and access policy are private per user.
    another_user = build_litellm_mcp_server_name("com.example/weather.mcp", "user-2")

    # Besides uniqueness, keep a readable prefix so operators can recognize the
    # MCP in LiteLLM without relying only on the generated hash.
    assert first == second
    assert first != another_user
    assert first.startswith("papi_weather_mcp_")


def test_create_remote_mcp_server_sends_owner_and_registry_metadata():
    # Simulate a successful LiteLLM registration without making a real HTTP call.
    # The injected session also lets the test inspect the exact management payload.
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"server_id": "litellm-id"}
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.post.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example/",
        api_key="secret",
        timeout=15,
        session=session,
    )
    registry_server = {
        "name": "com.example/weather",
        "title": "Weather",
        "description": "Weather tools",
        "version": "2.0.0",
    }

    # Register a remote MCP on behalf of the authenticated Keycloak user.
    result = client.create_remote_mcp_server(
        registry_server=registry_server,
        owner="keycloak-user-id",
        endpoint={"transport": "http", "url": "https://weather.example/mcp"},
    )

    # The server must start private. Access is added later through the user's
    # access group, never by making the MCP available to every LiteLLM key.
    assert result == {"server_id": "litellm-id"}
    payload = session.post.call_args.kwargs["json"]
    assert payload["allow_all_keys"] is False

    # The metadata connects LiteLLM back to the PAPI owner and registry source.
    # nomad_job_id remains empty because this case represents a remote MCP.
    assert payload["mcp_info"] == {
        "owner": "keycloak-user-id",
        "source": "official-mcp-registry",
        "registry_name": "com.example/weather",
        "registry_version": "2.0.0",
        "deployment_type": "remote",
        "nomad_job_id": None,
    }

    # Check that registration uses the MCP management endpoint, rather than a
    # generic server endpoint whose payload could have different semantics.
    assert session.post.call_args.args == ("https://litellm.example/v1/mcp/server",)


def test_mcp_access_group_name_is_stable_per_owner_and_server():
    # Case 1 — a retry for the same owner/server pair reuses the same group name.
    first = build_litellm_mcp_access_group_name("user-1", "server-1")
    second = build_litellm_mcp_access_group_name("user-1", "server-1")

    # Case 2 — another user receives a different group even for the same server ID,
    # preventing two private policies from accidentally sharing a name.
    another_user = build_litellm_mcp_access_group_name("user-2", "server-1")

    # The prefix makes explicit that this is a PAPI-managed MCP access group.
    assert first == second
    assert first != another_user
    assert first.startswith("papi_mcp_")


def test_list_user_mcp_key_ids_returns_only_valid_key_identifiers():
    # LiteLLM can return records without a usable token identifier. They must be
    # ignored because assigned_key_ids only accepts real stored key identifiers.
    list_response = Mock()
    list_response.raise_for_status.return_value = None
    list_response.json.return_value = {
        "keys": [
            {"token": "hashed-key-1"},
            {"token": "hashed-key-2"},
            {"token": None},
        ]
    }
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = list_response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    # Only the two valid keys are candidates for the private MCP access group.
    assert client.list_user_mcp_key_ids("user-id") == [
        "hashed-key-1",
        "hashed-key-2",
    ]

    # return_full_object is required because the access-group API needs the stored
    # token identifier, not merely the human-readable key alias.
    session.get.assert_called_once_with(
        "https://litellm.example/key/list",
        params={"user_id": "user-id", "return_full_object": "true"},
        timeout=15,
    )


def test_create_mcp_access_group_assigns_server_directly_to_user_keys():
    # Simulate LiteLLM returning the ID of the newly created unified access group.
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"access_group_id": "group-id"}
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.post.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    # This represents a user who already has two virtual keys when creating an MCP.
    result = client.create_mcp_access_group(
        owner="user-id",
        server_id="server-id",
        key_ids=["hashed-key-1", "hashed-key-2"],
    )

    assert result == {"access_group_id": "group-id"}
    payload = session.post.call_args.kwargs["json"]

    # The group grants exactly the newly created server to the owner's keys.
    assert payload["access_mcp_server_ids"] == ["server-id"]
    assert payload["assigned_key_ids"] == ["hashed-key-1", "hashed-key-2"]

    # Critical privacy case: never assign the group to a shared team such as ap-d,
    # because every other member of that team would inherit the private MCP.
    assert payload["assigned_team_ids"] == []
    assert payload["access_group_name"].startswith("papi_mcp_")
    assert session.post.call_args.args == ("https://litellm.example/v1/access_group",)


def test_update_mcp_server_metadata_uses_partial_update_endpoint():
    # The access group is created after the MCP, so its ID has to be persisted in a
    # second, partial server update without resending connection configuration.
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"server_id": "server-id", "mcp_info": {}}
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.put.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    metadata = {"owner": "user-id", "access_group_id": "group-id"}
    result = client.update_mcp_server_metadata("server-id", metadata)

    # Only server_id and mcp_info are sent: URL, transport and credentials remain
    # untouched in LiteLLM.
    assert result == {"server_id": "server-id", "mcp_info": {}}
    session.put.assert_called_once_with(
        "https://litellm.example/v1/mcp/server",
        json={"server_id": "server-id", "mcp_info": metadata},
        timeout=15,
    )


def test_delete_mcp_access_group_uses_litellm_management_endpoint():
    # A 204 represents successful revocation. LiteLLM is responsible for removing
    # this group ID from every key that referenced it.
    response = Mock()
    response.status_code = 204
    response.raise_for_status.return_value = None
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.delete.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    client.delete_mcp_access_group("group-id")

    # The group must be deleted by ID before deleting its associated MCP server.
    session.delete.assert_called_once_with(
        "https://litellm.example/v1/access_group/group-id",
        timeout=15,
    )


def test_delete_mcp_server_uses_litellm_management_endpoint():
    # Simulate the final step of deletion after the private group was revoked.
    response = Mock()
    response.raise_for_status.return_value = None
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.delete.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    client.delete_mcp_server("server-id")

    # Check that only the requested server ID is placed in the delete URL.
    session.delete.assert_called_once_with(
        "https://litellm.example/v1/mcp/server/server-id",
        timeout=15,
    )


def test_list_mcp_servers_uses_litellm_management_endpoint():
    # The client's admin key can retrieve the complete MCP catalogue. User-level
    # filtering is deliberately performed by a separate method below.
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = [{"server_id": "server-id"}]
    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.return_value = response
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    # Preserve LiteLLM's registration data while checking the management endpoint.
    assert client.list_mcp_servers() == [{"server_id": "server-id"}]
    session.get.assert_called_once_with(
        "https://litellm.example/v1/mcp/server",
        timeout=15,
    )


def test_list_user_accessible_mcp_servers_combines_all_grant_sources():
    # Build one server for every visibility case supported by PAPI, plus a foreign
    # private server that must be filtered out.
    responses = {
        "/v1/mcp/server": [
            # Case 1 — MCP created by the authenticated user.
            {"server_id": "owned", "mcp_info": {"owner": "user-id"}},
            # Case 2 — MCP explicitly available to every LiteLLM key.
            {"server_id": "public", "allow_all_keys": True},
            # Case 3 — traditional direct mcp_servers permission.
            {"server_id": "direct"},
            # Case 4 — unified access group assigned directly to a user key.
            {"server_id": "unified-group"},
            # Case 5 — legacy named MCP group inherited from a team.
            {"server_id": "group", "mcp_access_groups": ["research"]},
            # Negative case — belongs to another user and has no shared grant.
            {"server_id": "foreign", "mcp_info": {"owner": "other-user"}},
        ],
        "/key/list": {
            "keys": [
                # This key exercises the old direct permission representation.
                {
                    "team_id": "team-id",
                    "object_permission": {"mcp_servers": ["direct"]},
                },
                # This key has no object_permission: unified access_group_ids must
                # still be evaluated independently.
                {
                    "team_id": "team-id",
                    "access_group_ids": ["private-group-id"],
                },
            ]
        },
        # Explicit team membership covers grants available through a team even if
        # no current key were using that membership.
        "/user/info": {"teams": [{"team_id": "team-id"}]},
        # The researchers-like team grants the legacy group named "research".
        "/team/info": {
            "team_id": "team-id",
            "object_permission": {"mcp_access_groups": ["research"]},
        },
        # Resolving the unified group converts its ID into the permitted server ID.
        "/v1/access_group/private-group-id": {
            "access_group_id": "private-group-id",
            "access_mcp_server_ids": ["unified-group"],
        },
    }

    def get(url, **kwargs):
        # Route each mocked GET to the response belonging to that LiteLLM endpoint.
        response = Mock()
        response.raise_for_status.return_value = None
        path = url.removeprefix("https://litellm.example")
        response.json.return_value = responses[path]
        return response

    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = get
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    result = client.list_user_accessible_mcp_servers("user-id")

    # All positive cases are returned. The foreign private MCP is intentionally
    # absent because neither ownership nor any permission source grants it.
    assert {server["server_id"] for server in result} == {
        "owned",
        "public",
        "direct",
        "unified-group",
        "group",
    }


def test_list_user_accessible_mcp_servers_without_litellm_user():
    def get(url, **kwargs):
        # Simulate a Keycloak-authenticated user who has created an MCP through PAPI
        # but has not yet created a LiteLLM user or virtual key.
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        if url.endswith("/v1/mcp/server"):
            response.json.return_value = [
                {"server_id": "owned", "mcp_info": {"owner": "user-id"}},
                {"server_id": "public", "allow_all_keys": True},
                {"server_id": "foreign", "mcp_info": {"owner": "other-user"}},
            ]
        elif url.endswith("/key/list"):
            # With no virtual keys there are no direct or group grants to resolve.
            response.json.return_value = {"keys": []}
        elif url.endswith("/user/info"):
            # A missing LiteLLM user is a valid read-only listing case, not an error.
            response.status_code = 404
            response.json.return_value = {"error": "User not found"}
        return response

    session = Mock(spec=requests.Session)
    session.headers = {}
    session.get.side_effect = get
    client = LiteLLMClient(
        base_url="https://litellm.example",
        api_key="secret",
        timeout=15,
        session=session,
    )

    result = client.list_user_accessible_mcp_servers("user-id")

    # Ownership and public visibility do not require a LiteLLM user row. The MCP
    # belonging to another user remains hidden.
    assert {server["server_id"] for server in result} == {"owned", "public"}

    # Listing must stay read-only: it must not create the absent user automatically.
    session.post.assert_not_called()
