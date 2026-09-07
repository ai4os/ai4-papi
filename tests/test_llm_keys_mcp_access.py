from types import SimpleNamespace
from unittest.mock import Mock

from ai4papi.routers.v1.llm import keys


def test_new_key_receives_access_groups_from_owned_mcps(monkeypatch):
    # This is the future-key case: the user belongs to the shared ap-d team and
    # creates a virtual key after having registered private MCP servers.
    monkeypatch.setattr(
        keys.auth,
        "get_user_info",
        lambda token: {"id": "user-id", "groups": {"ap-d": ["vo.ai4eosc.eu"]}},
    )
    monkeypatch.setattr(keys.auth, "get_highest_level", lambda levels: "ap-d")

    # Two MCPs belong to the authenticated user and reference private access groups.
    # A third MCP belongs to another user and must never affect the new key.
    list_response = Mock()
    list_response.json.return_value = [
        {
            "server_id": "owned-1",
            "mcp_info": {"owner": "user-id", "access_group_id": "group-2"},
        },
        {
            "server_id": "owned-2",
            "mcp_info": {"owner": "user-id", "access_group_id": "group-1"},
        },
        {
            "server_id": "foreign",
            "mcp_info": {"owner": "other-user", "access_group_id": "group-3"},
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

    # Only access groups from MCPs owned by this user are attached. Sorting makes
    # the generated payload stable even if LiteLLM returns servers in another order.
    assert payload["access_group_ids"] == ["group-1", "group-2"]

    # Direct mcp_servers permissions are deliberately absent: that old mechanism
    # was rejected when the key requested more MCPs than its ap-d team allowed.
    assert "object_permission" not in payload
