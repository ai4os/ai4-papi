import time
from types import SimpleNamespace

from ai4papi.routers.v1.llm import mcp

from conf import token


MCPS_TO_TEST = [
    "dev.whenever/whenever",  # example of remote MCP
    "com.adrianczuczka/mason",  # example of deployable MCP (NPM)
]
VO = "vo.ai4eosc.eu"


auth = SimpleNamespace(credentials=token)
registry = mcp.get_registry_client()
litellm = mcp.get_litellm_client()
nomad = mcp.get_mcp_nomad_client()

for mcp_name in MCPS_TO_TEST:
    print(f"  Testing MCP: {mcp_name}")

    # List mcps
    mcps = mcp.list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert isinstance(mcps, list)

    # Check the mcp is not there, otherwise delete
    for m in mcps:
        if m.get("name") == mcp_name:
            server_id = m["registration"]["server_id"]
            mcp.delete_mcp(
                server_id=server_id,
                authorization=auth,
                litellm=litellm,
                nomad=nomad,
            )

    mcps = mcp.list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert not any(m.get("name") == mcp_name for m in mcps)

    # Deploy the mcp
    created = mcp.create_mcp(
        request=mcp.MCPCreateRequest(name=mcp_name, vo=VO),
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
        mcps = mcp.list_mcps(
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

    mcp.delete_mcp(
        server_id=server_id,
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )

    # Check the mcp is not there
    mcps = mcp.list_mcps(
        authorization=auth,
        litellm=litellm,
        nomad=nomad,
    )
    assert not any(m.get("name") == mcp_name for m in mcps)

print("🟢 AI4OS LLM (MCP) tests passed!")
