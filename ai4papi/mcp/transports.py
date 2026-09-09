"""Expose MCP packages through transports understood by LiteLLM."""

import base64
from dataclasses import dataclass
import hashlib
import re
import secrets
import shlex
from typing import Any
from urllib.parse import urlsplit

from ai4papi.mcp.packages import (
    MCPPackageNotDeployableError,
    build_npm_mcp_command,
    resolve_registry_input,
)


STDIO_GATEWAY_PORT = 8000
STDIO_GATEWAY_PATH = "/mcp"


@dataclass(frozen=True)
class MCPBasicAuth:
    """The two representations needed by Traefik and LiteLLM."""

    username: str
    password_hash: str
    authorization_header: str


def create_mcp_basic_auth() -> MCPBasicAuth:
    """
    Generate one high-entropy Basic Auth credential for a deployed MCP.
    Auth is managed through Traefik middleware on Nomad.
    LiteLLM authenticases using Static Header Basic Auth.
    """

    username = "papi"
    password = secrets.token_urlsafe(32)

    # Traefik accepts Apache htpasswd SHA-1 entries. The password itself has 256
    # random bits and is never stored in Nomad; only this verifier is published in
    # the Traefik tag. LiteLLM receives the corresponding Basic header below.
    password_digest = hashlib.sha1(password.encode()).digest()
    password_hash = "{SHA}" + base64.b64encode(password_digest).decode()
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()

    return MCPBasicAuth(
        username=username,
        password_hash=password_hash,
        authorization_header=f"Basic {credentials}",
    )


def build_stdio_gateway_command(
    package: dict[str, Any],
    gateway_package: str,
) -> tuple[str, dict[str, str]]:
    """
    Build a shell bootstrap that exposes one stdio package over HTTP.
    """

    # In order tu support STDIO, which i think is more common than Streamable HTTP
    # or SSE (maybe not), we need to use a gateway package that will expose the stdio package over HTTP.

    # For the moment i suggest using Supergateway.

    if (package.get("transport") or {}).get("type") != "stdio":
        raise MCPPackageNotDeployableError(
            "The selected npm package does not declare stdio transport."
        )

    # Gateway package comes from PAPI configuration. It is not currently exposed to the user, so value should be safe.
    # However, validation does not hurt.

    if not isinstance(gateway_package, str) or not re.fullmatch(
        r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*"
        r"@[0-9A-Za-z][0-9A-Za-z._+-]*",
        gateway_package,
    ):
        raise MCPPackageNotDeployableError(
            "MCP_NOMAD_SUPERGATEWAY_PACKAGE must contain a pinned valid npm package."
        )

    command_arguments, environment = build_npm_mcp_command(package)
    package_reference = f"{package['identifier']}@{package['version']}"
    runtime_directory = "/tmp/papi-mcp-runtime"

    # Both packages are installed in one dependency tree before either process
    # starts. The child npx runs offline from that tree, avoiding the competing
    # temporary `_npx` installations that previously produced TAR_ENTRY_ERROR and
    # partially installed @modelcontextprotocol/sdk modules.
    install_command = shlex.join(
        [
            "npm",
            "install",
            "--prefix",
            runtime_directory,
            "--no-save",
            "--package-lock=false",
            "--no-audit",
            "--no-fund",
            gateway_package,
            package_reference,
        ]
    )
    child_command = shlex.join(["npx", "--offline", *command_arguments])
    gateway_binary = f"{runtime_directory}/node_modules/.bin/supergateway"
    gateway_command = shlex.join(
        [
            gateway_binary,
            "--stdio",
            child_command,
            "--outputTransport",
            "streamableHttp",
            "--port",
            str(STDIO_GATEWAY_PORT),
            "--streamableHttpPath",
            STDIO_GATEWAY_PATH,
            "--healthEndpoint",
            "/healthz",
        ]
    )
    script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {shlex.quote(runtime_directory)}",
            install_command,
            f"cd {shlex.quote(runtime_directory)}",
            f"exec {gateway_command}",
        ]
    )
    return script, environment


def resolve_streamable_http_package_transport(
    package: dict[str, Any],
    environment: dict[str, str],
) -> tuple[int, str]:
    """Resolve the local port and path exposed by a Streamable HTTP package."""

    transport = package.get("transport") or {}
    if transport.get("type") != "streamable-http":
        raise MCPPackageNotDeployableError(
            "The selected npm package does not declare Streamable HTTP transport."
        )
    if transport.get("headers"):
        raise MCPPackageNotDeployableError(
            "Streamable HTTP packages requiring transport headers are not supported yet."
        )

    url = transport.get("url")
    if not isinstance(url, str):
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package does not declare its local endpoint URL."
        )

    # Package URLs may refer to environment variables or argument valueHints,
    # for example http://{HOST}:{PORT}/mcp. Only already resolved Registry values
    # are substituted; PAPI never guesses missing user configuration.
    transport_variables = dict(environment)
    for argument in [
        *(package.get("runtimeArguments") or []),
        *(package.get("packageArguments") or []),
    ]:
        value = resolve_registry_input(argument, "transport URL")
        if value is None:
            continue
        variable_names = {
            argument.get("valueHint"),
            argument.get("name"),
        }
        argument_name = argument.get("name")
        if isinstance(argument_name, str):
            variable_names.add(argument_name.lstrip("-").replace("-", "_"))
        for variable_name in variable_names:
            if isinstance(variable_name, str) and variable_name:
                transport_variables[variable_name] = value

    resolved_url = url
    for name, value in transport_variables.items():
        resolved_url = resolved_url.replace(f"{{{name}}}", value)
    if "{" in resolved_url or "}" in resolved_url:
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package URL contains unresolved variables."
        )

    try:
        parsed = urlsplit(resolved_url)
        port = parsed.port
    except ValueError as exc:
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package declares an invalid endpoint URL."
        ) from exc

    # Traefik forwards plain HTTP to the allocation. A package URL pointing to an
    # external host is not evidence that the npm process starts a local server.
    if parsed.scheme != "http" or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }:
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package must declare a local http:// endpoint."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package endpoint cannot contain credentials, "
            "a query string or a fragment."
        )
    if port is None or port < 1024:
        raise MCPPackageNotDeployableError(
            "The Streamable HTTP package must declare an explicit unprivileged port."
        )

    return port, parsed.path or "/"
