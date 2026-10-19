import time
from types import SimpleNamespace

from ai4papi.routers.v1.llm.mcp import (
    MCPCreateRequest,
    create_mcp,
    delete_mcp,
    get_litellm_client,
    get_mcp_nomad_client,
    get_registry_client,
    list_mcps,
)
from conf import token

auth = SimpleNamespace(credentials=token)
registry = get_registry_client()
litellm = get_litellm_client()
nomad = get_mcp_nomad_client()

mcps_to_test = [
    "dev.whenever/whenever",  # example of remote MCP
    "io.github.GuardBee/security-proxy",  # example of deployable MCP (NPM)
]

for mcp_name in mcps_to_test:
    # List mcps
    mcps = list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert isinstance(mcps, list)

    # Check the mcp is not there, otherwise delete
    for m in mcps:
        if m.get("name") == mcp_name:
            server_id = m["registration"]["server_id"]
            delete_mcp(
                server_id=server_id,
                authorization=auth,
                litellm=litellm,
                nomad=nomad,
            )

    mcps = list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert not any(m.get("name") == mcp_name for m in mcps)

    # Deploy the mcp
    created = create_mcp(
        request=MCPCreateRequest(name=mcp_name),
        authorization=auth,
        registry=registry,
        litellm=litellm,
        nomad=nomad,
    )
    assert isinstance(created, dict)
    assert created.get("name") == mcp_name

    # List mcps and check mcp is there
    # For self-deployed MCPs, Nomad job starts and poststart task registers in LiteLLM;
    # poll until visible in list_mcps.
    server_id = None
    if created.get("registration") and created["registration"].get("server_id"):
        server_id = created["registration"]["server_id"]

    for _ in range(30):
        mcps = list_mcps(
            authorization=auth,
            litellm=litellm,
            nomad=nomad,
        )
        matching = [m for m in mcps if m.get("name") == mcp_name]
        if matching:
            if not server_id and matching[0].get("registration"):
                server_id = matching[0]["registration"].get("server_id")
            break
        time.sleep(2)

    assert any(m.get("name") == mcp_name for m in mcps)

    # Delete mcp
    if not server_id:
        matching = [m for m in mcps if m.get("name") == mcp_name]
        assert matching
        server_id = matching[0]["registration"]["server_id"]

    delete_mcp(
        server_id=server_id,
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )

    # Check the mcp is not there
    mcps = list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert not any(m.get("name") == mcp_name for m in mcps)

print("🟢 AI4OS LLM (MCP) tests passed!")
