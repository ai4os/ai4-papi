from types import SimpleNamespace
from unittest.mock import Mock

from ai4papi.litellm_client import build_litellm_mcp_access_group_name
from ai4papi.routers.v1.llm import keys


def test_new_key_receives_the_users_single_mcp_access_group(monkeypatch):
    # This is the future-key case: the user belongs to the shared ap-d team and
    # creates a virtual key after having registered private MCP servers.
    monkeypatch.setattr(
        keys.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-d": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(keys.auth, "get_highest_level", lambda levels: "ap-d")

    # LiteLLM can contain many unified groups, but the deterministic name selects
    # exactly the one PAPI created for this Keycloak user.
    list_response = Mock()
    list_response.json.return_value = [
        {
            "access_group_id": "user-group",
            "access_group_name": build_litellm_mcp_access_group_name("user-id"),
            "access_mcp_server_ids": ["owned-1", "owned-2"],
        },
        {
            "access_group_id": "another-user-group",
            "access_group_name": build_litellm_mcp_access_group_name("other-user"),
            "access_mcp_server_ids": ["foreign"],
        },
    ]
    generate_response = Mock()
    generate_response.json.return_value = {"key": "sk-new"}

    # Replace keys.py's legacy shared session so this test only inspects the
    # /key/generate payload and does not call the deployed LiteLLM instance.
    session = Mock()
    session.get.return_value = list_response
    session.post.return_value = generate_response
    monkeypatch.setattr(keys, "session", session)

    result = keys.create_api_key(
        key_name="test",
        authorization=SimpleNamespace(credentials="oidc-token"),
    )

    assert result == "sk-new"
    payload = session.post.call_args.kwargs["json"]

    # The key keeps its normal authorization-level team. Private MCP ownership does
    # not create a new team and does not modify the permissions of ap-d.
    assert payload["team_id"] == "ap-d"

    # A key points to one user group regardless of how many MCPs that group grants.
    assert payload["access_group_ids"] == ["user-group"]

    # Direct mcp_servers permissions are deliberately absent: that old mechanism
    # was rejected when the key requested more MCPs than its ap-d team allowed.
    assert "object_permission" not in payload
