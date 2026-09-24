"""Select and configure runnable MCP packages from Registry metadata."""

from copy import deepcopy
import re
from typing import Any


class MCPPackageNotDeployableError(RuntimeError):
    """The registry package cannot be deployed by the current Nomad runner."""


def select_npm_mcp_package(
    registry_server: dict[str, Any],
) -> dict[str, Any]:
    """Select a supported npm package, preferring native Streamable HTTP."""

    # Prefer a package that already exposes Streamable HTTP. It needs no adapter,
    # starts faster and has fewer moving parts than the equivalent stdio package.
    for transport_type in ("streamable-http", "stdio"):
        for package in registry_server.get("packages", []):
            if (
                package.get("registryType") == "npm"
                and (package.get("transport") or {}).get("type") == transport_type
            ):
                # Package versions are optional in the Registry schema. In that
                # case the server version still pins the published npm release.
                selected = deepcopy(package)
                selected.setdefault("version", registry_server.get("version"))
                return selected

    # TODO Support SSE transport
    raise MCPPackageNotDeployableError(
        f'The MCP server "{registry_server["name"]}" has no supported npm '
        "package. Self-deployment supports npm packages using Streamable HTTP "
        "or stdio; SSE is not supported yet."
    )


def resolve_registry_input(item: dict[str, Any], context: str) -> str | None:
    """Resolve a fixed/default registry input, rejecting missing required values."""

    value = item.get("value", item.get("default"))
    if value is None:
        if item.get("isRequired", False):
            name = item.get("name", context)
            raise MCPPackageNotDeployableError(
                f'The required MCP package input "{name}" needs a user-provided '
                "value, which this endpoint does not support yet."
            )
        return None

    value = str(value)
    variables = item.get("variables") or {}
    for variable_name, variable in variables.items():
        variable_value = variable.get("value", variable.get("default"))
        if variable_value is not None:
            value = value.replace(f"{{{variable_name}}}", str(variable_value))

    # An unresolved placeholder would be passed literally to the package and would
    # normally create a broken or misleading deployment, so fail before Nomad runs.
    if re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", value):
        raise MCPPackageNotDeployableError(
            f'The MCP package input for "{context}" contains unresolved variables.'
        )

    return value


def _resolve_registry_arguments(
    arguments: list[dict[str, Any]],
    context: str,
) -> list[str]:
    """Convert typed Registry arguments into an argv list without using a shell."""

    resolved: list[str] = []
    for argument in arguments:
        value = resolve_registry_input(argument, context)
        if value is None:
            continue

        argument_type = argument.get("type")
        if argument_type == "positional":
            resolved.append(value)
        elif argument_type == "named":
            name = argument.get("name")
            if not isinstance(name, str) or not name.startswith("-"):
                raise MCPPackageNotDeployableError(
                    f'The MCP package contains an invalid named argument in "{context}".'
                )
            resolved.append(f"{name}={value}")
        else:
            raise MCPPackageNotDeployableError(
                f'The MCP package contains an unsupported argument type in "{context}".'
            )

    return resolved


def build_npm_mcp_command(package: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """Build the pinned direct npx command and package environment."""

    identifier = package.get("identifier")  # Example: "@ai4papi/stdio-mcp"
    if not isinstance(identifier, str) or not re.fullmatch(
        r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*",
        identifier,
    ):
        raise MCPPackageNotDeployableError(
            "The registry contains an invalid npm package name."
        )

    version = package.get("version")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9A-Za-z][0-9A-Za-z._+-]*",
        version,
    ):
        raise MCPPackageNotDeployableError(
            f'The npm package "{identifier}" does not declare a fixed valid version.'
        )

    registry_base_url = package.get("registryBaseUrl")
    if (
        registry_base_url
        and registry_base_url.rstrip("/") != "https://registry.npmjs.org"
    ):
        raise MCPPackageNotDeployableError(
            f'The npm package "{identifier}" uses an unsupported custom registry.'
        )

    runtime_hint = package.get("runtimeHint")
    if runtime_hint not in (None, "npx"):
        raise MCPPackageNotDeployableError(
            f'The npm package "{identifier}" requires unsupported runtime '
            f'"{runtime_hint}".'
        )

    runtime_arguments = _resolve_registry_arguments(
        package.get("runtimeArguments") or [],
        "runtimeArguments",
    )
    package_arguments = _resolve_registry_arguments(
        package.get("packageArguments") or [],
        "packageArguments",
    )

    package_reference = f"{identifier}@{version}"
    command_arguments = []
    has_package_reference = False
    for argument in runtime_arguments:
        if argument == identifier or argument.startswith(f"{identifier}@"):
            # Registry runtime arguments sometimes contain the package reference.
            # Replace it with the selected fixed version and discard duplicates.
            if not has_package_reference:
                command_arguments.append(package_reference)
                has_package_reference = True
        else:
            command_arguments.append(argument)
    if "-y" not in command_arguments and "--yes" not in command_arguments:
        command_arguments.insert(0, "-y")

    # Registry publishers currently use both conventions: some runtimeArguments
    # include the package reference and others leave it implicit. Ensure exactly
    # one pinned package identity is present in the final npx command.
    if not has_package_reference:
        command_arguments.append(package_reference)
    command_arguments.extend(package_arguments)

    environment: dict[str, str] = {}
    for variable in package.get("environmentVariables") or []:
        name = variable.get("name")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", name
        ):
            raise MCPPackageNotDeployableError(
                "The MCP package contains an invalid environment variable name."
            )
        value = resolve_registry_input(variable, name)
        if value is not None:
            environment[name] = value

    # Native HTTP jobs pass this list directly to npx. The stdio adapter applies
    # shell quoting to the same entries before giving the child command to its
    # gateway, so Registry values cannot become shell syntax in either path.
    return command_arguments, environment
