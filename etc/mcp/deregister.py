"""Nomad poststop task: remove one stopped MCP from central LiteLLM."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


central_url = os.environ["CENTRAL_LITELLM_URL"].rstrip("/")
central_key = os.environ["CENTRAL_LITELLM_API_KEY"]
job_id = os.environ["NOMAD_JOB_ID"]
access_group_name = os.environ["MCP_ACCESS_GROUP_NAME"]


def request_json(method, url, *, payload=None, ignore_not_found=False):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {central_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read()
    except HTTPError as exc:
        if ignore_not_found and exc.code == 404:
            return None
        response_body = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"{method} {url} failed with HTTP {exc.code}: {response_body[:1000]}"
        ) from exc
    return json.loads(response_body) if response_body else None


def as_list(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


servers = as_list(
    request_json("GET", f"{central_url}/v1/mcp/server"),
    "servers",
    "data",
)
registration = next(
    (
        server
        for server in servers
        if (server.get("mcp_info") or {}).get("nomad_job_id") == job_id
    ),
    None,
)

if registration is None:
    print(f"Nomad job {job_id} has no LiteLLM registration", flush=True)
    raise SystemExit(0)

server_id = registration["server_id"]
groups = as_list(
    request_json("GET", f"{central_url}/v1/access_group"),
    "access_groups",
    "data",
)
access_group = next(
    (group for group in groups if group.get("access_group_name") == access_group_name),
    None,
)

# Keep the user's stable group and revoke only the MCP backed by this stopped job.
if access_group is not None:
    remaining_server_ids = sorted(
        set(access_group.get("access_mcp_server_ids", [])) - {server_id}
    )
    request_json(
        "PUT",
        f"{central_url}/v1/access_group/{access_group['access_group_id']}",
        payload={"access_mcp_server_ids": remaining_server_ids},
    )

request_json(
    "DELETE",
    f"{central_url}/v1/mcp/server/{server_id}",
    ignore_not_found=True,
)
print(f"Removed MCP {server_id} for stopped Nomad job {job_id}", flush=True)
