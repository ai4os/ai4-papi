"""Client helpers for the official Model Context Protocol registry."""

from typing import Any, Literal, TypedDict

import requests

import ai4papi.conf as papiconf

OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"


class MCPRegistryError(RuntimeError):
    """The MCP registry could not be queried or returned an invalid response."""


class MCPServerNotFoundError(MCPRegistryError):
    """No active version of the requested MCP server exists in the registry."""


class MCPRegistryUnavailableError(MCPRegistryError):
    """The MCP registry could not be reached."""


class MCPRegistryTimeoutError(MCPRegistryUnavailableError):
    """The MCP registry did not respond before the configured timeout."""


class MCPServerInfo(TypedDict):
    name: str
    title: str | None
    description: str | None
    version: str
    kind: Literal["remote", "deployable", "hybrid"]
    remotes: list[dict[str, Any]]
    packages: list[dict[str, Any]]
    registry_metadata: dict[str, Any]


class MCPRemoteEndpoint(TypedDict):
    transport: Literal["http", "sse"]
    url: str


class MCPRegistryClient:
    """Small read-only client for the public MCP Registry API."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = (base_url or papiconf.MCP_REGISTRY_URL).rstrip("/")
        self.timeout = timeout or papiconf.MCP_REGISTRY_TIMEOUT
        self.session = session or requests.Session()

    def get_server(self, name: str) -> MCPServerInfo:
        """Return the latest active version whose registry name matches exactly."""
        try:
            response = self.session.get(
                f"{self.base_url}/v0.1/servers",
                params={"search": name, "limit": 100},
                timeout=self.timeout,
            )
            response.raise_for_status()
            entries = response.json().get("servers", [])
        except requests.Timeout as exc:
            raise MCPRegistryTimeoutError(
                f"The MCP registry did not respond within {self.timeout} seconds."
            ) from exc
        except requests.RequestException as exc:
            raise MCPRegistryUnavailableError(
                f"Unable to connect to the MCP registry: {exc}"
            ) from exc
        except (ValueError, AttributeError) as exc:
            raise MCPRegistryError(f"Unable to query the MCP registry: {exc}") from exc

        candidates = []
        for entry in entries:
            server = entry.get("server", {})
            metadata = entry.get("_meta", {}).get(OFFICIAL_META_KEY, {})

            if (
                server.get("name") == name
                and metadata.get("status") == "active"
                and metadata.get("isLatest") is True
            ):
                candidates.append((server, metadata))

        if not candidates:
            raise MCPServerNotFoundError(
                f'No latest active MCP server named "{name}" was found.'
            )
        if len(candidates) > 1:
            raise MCPRegistryError(
                f'The MCP registry returned multiple latest versions for "{name}".'
            )

        server, metadata = candidates[0]
        remotes = server.get("remotes", [])
        packages = server.get("packages", [])

        if remotes and packages:
            kind = "hybrid"
        elif remotes:
            kind = "remote"
        elif packages:
            kind = "deployable"
        else:
            raise MCPRegistryError(
                f'The MCP server "{name}" has neither remotes nor packages.'
            )

        return {
            "name": server["name"],
            "title": server.get("title"),
            "description": server.get("description"),
            "version": server["version"],
            "kind": kind,
            "remotes": remotes,
            "packages": packages,
            "registry_metadata": metadata,
        }


def select_remote_endpoint(server: MCPServerInfo) -> MCPRemoteEndpoint:
    """Select a remote that can be registered without additional user input."""

    transport_map = {"streamable-http": "http", "sse": "sse"}

    remotes = sorted(
        server["remotes"],
        key=lambda remote: remote.get("type") != "streamable-http",
    )

    for remote in remotes:
        remote_type = remote.get("type")
        url = remote.get("url")
        has_required_headers = any(
            header.get("isRequired", False) for header in remote.get("headers", [])
        )

        # The remote is usable if it has a supported transport, a valid URL, and does not require additional headers.
        if (
            remote_type in transport_map
            and isinstance(url, str)
            and "{" not in url
            and "}" not in url
            and not has_required_headers
        ):
            return {
                "transport": transport_map[remote_type],
                "url": url,
            }

    raise MCPRegistryError(
        f'The remote MCP server "{server["name"]}" requires configuration '
        "or has no supported HTTP/SSE endpoint."
    )
