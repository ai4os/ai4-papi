from unittest.mock import Mock

import pytest
import requests

from ai4papi.mcp.registry import (
    MCPRegistryClient,
    MCPRegistryError,
    MCPRegistryTimeoutError,
    MCPServerNotFoundError,
    OFFICIAL_META_KEY,
    select_remote_endpoint,
)


def registry_entry(server, *, latest=True, status="active"):
    # Reproduce the envelope returned by the official registry. Status and latest
    # live in the official metadata rather than inside the server description.
    return {
        "server": server,
        "_meta": {
            OFFICIAL_META_KEY: {
                "status": status,
                "isLatest": latest,
                "publishedAt": "2026-01-01T00:00:00Z",
            }
        },
    }


def client_with(entries):
    # Inject a fake HTTP session so registry selection can be tested deterministically
    # without depending on Internet availability or the live registry contents.
    response = Mock()
    response.json.return_value = {"servers": entries}
    response.raise_for_status.return_value = None
    session = Mock(spec=requests.Session)
    session.get.return_value = response
    return MCPRegistryClient(timeout=10, session=session), session


def test_get_server_selects_latest_active_exact_match():
    name = "io.modelcontextprotocol/server-everything"

    # Case 1 — an older version with the exact name must not be selected.
    old = {"name": name, "version": "1.0.0", "packages": [{}]}

    # Case 2 — the latest active exact match is deployable because it publishes an
    # npm package but no already-hosted remote endpoint.
    latest = {
        "name": name,
        "title": "Everything",
        "version": "1.1.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@modelcontextprotocol/server-everything",
                "version": "1.1.0",
                "transport": {"type": "stdio"},
            }
        ],
    }

    # Case 3 — registry search may return similar names. A fuzzy match must never
    # cause PAPI to register a different MCP than the one the user requested.
    similar_name = {
        "name": f"{name}-unsafe",
        "version": "9.0.0",
        "remotes": [{"type": "sse", "url": "https://example.test/sse"}],
    }
    client, session = client_with(
        [
            registry_entry(old, latest=False),
            registry_entry(latest),
            registry_entry(similar_name),
        ]
    )

    result = client.get_server(name)

    # Confirm both version selection and deployable/remote classification.
    assert result["name"] == name
    assert result["version"] == "1.1.0"
    assert result["kind"] == "deployable"

    # The client searches the official endpoint with an explicit result limit and
    # then performs its own exact-match filtering.
    session.get.assert_called_once_with(
        "https://registry.modelcontextprotocol.io/v0.1/servers",
        params={"search": name, "limit": 100},
        timeout=10.0,
    )


@pytest.mark.parametrize(
    ("server", "kind"),
    [
        (
            {
                "name": "example/remote",
                "version": "1.0.0",
                "remotes": [{"type": "streamable-http", "url": "https://x/mcp"}],
            },
            "remote",
        ),
        (
            {
                "name": "example/hybrid",
                "version": "1.0.0",
                "remotes": [{"type": "sse", "url": "https://x/sse"}],
                "packages": [{"registryType": "npm", "identifier": "example"}],
            },
            "hybrid",
        ),
    ],
)
def test_get_server_classifies_installation_kind(server, kind):
    # The parametrized cases cover a purely hosted MCP and a hybrid MCP that can be
    # either consumed remotely or deployed from a package in a later Nomad phase.
    client, _ = client_with([registry_entry(server)])

    assert client.get_server(server["name"])["kind"] == kind


def test_get_server_rejects_missing_server():
    # An empty search result must become a domain-specific not-found error so the
    # router can return HTTP 404 rather than a generic registry failure.
    client, _ = client_with([])

    with pytest.raises(MCPServerNotFoundError):
        client.get_server("missing/server")


def test_get_server_reports_registry_timeout():
    # Simulate the registry not answering before the configured deadline. This case
    # is distinct from a missing MCP and is translated to HTTP 504 by the router.
    session = Mock(spec=requests.Session)
    session.get.side_effect = requests.Timeout("timed out")

    # Set the timeout explicitly so this unit test does not depend on the value of
    # MCP_REGISTRY_TIMEOUT configured in the developer's local .env file.
    client = MCPRegistryClient(timeout=10, session=session)

    with pytest.raises(MCPRegistryTimeoutError, match="within 10 seconds"):
        client.get_server("example/server")


def test_select_remote_endpoint_accepts_streamable_http_and_ignores_sse():
    # PAPI currently supports one remote transport. An SSE entry must not be used
    # even when the Registry returns it before the Streamable HTTP endpoint.
    server = {
        "name": "example/server",
        "remotes": [
            {"type": "sse", "url": "https://example.test/sse"},
            {"type": "streamable-http", "url": "https://example.test/mcp"},
        ],
    }

    assert select_remote_endpoint(server) == {
        "transport": "http",
        "url": "https://example.test/mcp",
    }


def test_select_remote_endpoint_rejects_required_configuration():
    # This remote cannot be registered automatically: its URL has a placeholder and
    # it requires a user-provided header that PAPI does not collect yet.
    server = {
        "name": "example/server",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://{tenant}.example.test/mcp",
                "headers": [{"name": "X-API-Key", "isRequired": True}],
            }
        ],
    }

    # Rejecting it prevents creation of a LiteLLM server that could never connect.
    with pytest.raises(MCPRegistryError, match="requires configuration"):
        select_remote_endpoint(server)


def test_select_remote_endpoint_rejects_sse_only_server():
    # SSE support is intentionally deferred. Rejecting it explicitly prevents the
    # transport from entering LiteLLM through an untested compatibility path.
    server = {
        "name": "example/server",
        "remotes": [{"type": "sse", "url": "https://example.test/sse"}],
    }

    with pytest.raises(MCPRegistryError, match="SSE is not supported yet"):
        select_remote_endpoint(server)
