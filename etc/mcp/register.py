"""Nomad poststart task: register one running MCP in central LiteLLM."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


central_url = os.environ["CENTRAL_LITELLM_URL"].rstrip("/")
central_key = os.environ["CENTRAL_LITELLM_API_KEY"]
local_mcp_url = os.environ["LOCAL_MCP_URL"]
public_mcp_url = os.environ["PUBLIC_MCP_URL"]
owner = os.environ["MCP_OWNER"]
server_name = os.environ["MCP_SERVER_NAME"]
access_group_name = os.environ["MCP_ACCESS_GROUP_NAME"]
job_id = os.environ["NOMAD_JOB_ID"]
startup_timeout = float(os.environ["MCP_STARTUP_TIMEOUT"])
poll_interval = float(os.environ["MCP_POLL_INTERVAL"])


def request_json(method, url, *, payload=None, query=None, headers=None):
    """Send one JSON request and include the response body in any failure."""

    if query:
        url = f"{url}?{urlencode(query)}"
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = {
        "Authorization": f"Bearer {central_key}",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read()
    except HTTPError as exc:
        response_body = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"{method} {url} failed with HTTP {exc.code}: {response_body[:1000]}"
        ) from exc
    if not response_body:
        return None
    return json.loads(response_body)


def as_list(payload, *keys):
    """Normalize LiteLLM list responses across compatible proxy versions."""

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def is_initialize_result(payload):
    """Return whether a JSON-RPC payload confirms MCP initialization."""

    return (
        isinstance(payload, dict)
        and payload.get("id") == "nomad-register"
        and isinstance(payload.get("result"), dict)
        and payload.get("error") is None
    )


def read_initialize_result(response):
    """Read an initialize result returned either as JSON or as an SSE event."""

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
    if content_type == "text/event-stream":
        # An SSE response may remain open after the first event. Stop reading as
        # soon as the matching JSON-RPC result arrives instead of waiting for EOF.
        while line := response.readline():
            decoded = line.decode(errors="replace").strip()
            if not decoded.startswith("data:"):
                continue
            payload = json.loads(decoded.removeprefix("data:").strip())
            if is_initialize_result(payload):
                return payload
        raise RuntimeError("MCP closed its SSE response before initialize completed")

    body = response.read()
    if not body:
        raise RuntimeError("MCP returned an empty initialize response")
    payload = json.loads(body)
    if not is_initialize_result(payload):
        raise RuntimeError("MCP did not return a successful initialize result")
    return payload


def wait_for_local_mcp():
    """Wait for the native or stdio-adapted HTTP endpoint before publishing it."""

    deadline = time.monotonic() + startup_timeout
    initialize = {
        "jsonrpc": "2.0",
        "id": "nomad-register",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "ai4-papi-nomad", "version": "1.0.0"},
        },
    }
    while time.monotonic() < deadline:
        try:
            request = Request(
                local_mcp_url,
                data=json.dumps(initialize).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                method="POST",
            )
            # Supergateway can accept the HTTP request even when its stdio child
            # immediately exits. Require the actual JSON-RPC result so such a job
            # never gets registered in LiteLLM as healthy.
            with urlopen(request, timeout=10) as response:
                read_initialize_result(response)
            return
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            print(f"Waiting for MCP at {local_mcp_url}: {exc}", flush=True)
            time.sleep(poll_interval)
    raise TimeoutError(f"MCP did not become ready within {startup_timeout} seconds")


def mcp_servers():
    return as_list(
        request_json("GET", f"{central_url}/v1/mcp/server"),
        "servers",
        "data",
    )


def find_registration():
    """Find this job's registration so task restarts remain idempotent."""

    for server in mcp_servers():
        metadata = server.get("mcp_info") or {}
        if metadata.get("nomad_job_id") == job_id:
            return server
    return None


wait_for_local_mcp()

mcp_info = {
    "owner": owner,
    "source": "official-mcp-registry",
    "registry_name": os.environ["MCP_REGISTRY_NAME"],
    "registry_version": os.environ["MCP_REGISTRY_VERSION"],
    "deployment_type": "self_deployed",
    "nomad_job_id": job_id,
    "nomad_namespace": os.environ["NOMAD_NAMESPACE"],
    "package_registry_type": os.environ["MCP_PACKAGE_REGISTRY_TYPE"],
    "package_identifier": os.environ["MCP_PACKAGE_IDENTIFIER"],
    "package_version": os.environ["MCP_PACKAGE_VERSION"],
    # Keep both sides of the adapter visible: stdio describes the Registry
    # package, while LiteLLM always connects to the exposed Streamable HTTP URL.
    "package_transport": os.environ["MCP_PACKAGE_TRANSPORT"],
    "exposed_transport": "streamable-http",
}

registration = find_registration()
if registration is None:
    registration = request_json(
        "POST",
        f"{central_url}/v1/mcp/server",
        payload={
            "server_name": server_name,
            "description": os.environ.get("MCP_DESCRIPTION") or None,
            "transport": "http",
            "url": public_mcp_url,
            "allow_all_keys": False,
            "static_headers": {"Authorization": os.environ["MCP_AUTHORIZATION_HEADER"]},
            "mcp_info": mcp_info,
        },
    )

server_id = registration["server_id"]

# A user has exactly one PAPI MCP access group. Refreshing the complete key and
# server sets makes retries safe and also grants this MCP to keys created earlier.
keys_payload = request_json(
    "GET",
    f"{central_url}/key/list",
    query={"user_id": owner, "return_full_object": "true"},
)
key_ids = sorted(
    key["token"] for key in as_list(keys_payload, "keys") if key.get("token")
)
groups = as_list(
    request_json("GET", f"{central_url}/v1/access_group"),
    "access_groups",
    "data",
)
access_group = next(
    (group for group in groups if group.get("access_group_name") == access_group_name),
    None,
)

if access_group is None:
    access_group = request_json(
        "POST",
        f"{central_url}/v1/access_group",
        payload={
            "access_group_name": access_group_name,
            "description": "Private PAPI MCP access for one Keycloak user",
            "access_mcp_server_ids": [server_id],
            "assigned_key_ids": key_ids,
            "assigned_team_ids": [],
        },
    )
else:
    server_ids = sorted({*access_group.get("access_mcp_server_ids", []), server_id})
    access_group = request_json(
        "PUT",
        f"{central_url}/v1/access_group/{access_group['access_group_id']}",
        payload={
            "access_mcp_server_ids": server_ids,
            "assigned_key_ids": key_ids,
            "assigned_team_ids": [],
        },
    )

mcp_info.update(
    {
        "access_group_id": access_group["access_group_id"],
        "access_group_name": access_group_name,
    }
)
request_json(
    "PUT",
    f"{central_url}/v1/mcp/server",
    payload={"server_id": server_id, "mcp_info": mcp_info},
)
print(f"Registered MCP {server_id} for Nomad job {job_id}", flush=True)
