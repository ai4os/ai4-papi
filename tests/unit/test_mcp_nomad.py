from string import Template
from unittest.mock import Mock

import pytest

from ai4papi.mcp.nomad import MCPNomadClient
from ai4papi.mcp.packages import (
    MCPPackageNotDeployableError,
    build_npm_mcp_command,
    select_npm_mcp_package,
)
from ai4papi.mcp.transports import (
    build_stdio_gateway_command,
    resolve_streamable_http_package_transport,
)


def test_select_npm_package_inherits_server_version():
    # Registry package versions are optional. The surrounding server version still
    # lets PAPI run an immutable npm reference instead of silently using `latest`.
    package = select_npm_mcp_package(
        {
            "name": "com.example/weather",
            "version": "2.1.0",
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": "@example/weather-mcp",
                    "transport": {
                        "type": "streamable-http",
                        "url": "http://localhost:3001/mcp",
                    },
                }
            ],
        }
    )

    assert package["version"] == "2.1.0"
    assert package["identifier"] == "@example/weather-mcp"


def test_select_npm_package_prefers_native_http_over_stdio():
    # A Registry server may publish more than one runnable variant. Native HTTP
    # avoids an adapter, so it wins even when the stdio entry appears first.
    package = select_npm_mcp_package(
        {
            "name": "com.example/weather",
            "version": "2.1.0",
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": "@example/weather-stdio",
                    "transport": {"type": "stdio"},
                },
                {
                    "registryType": "npm",
                    "identifier": "@example/weather-http",
                    "transport": {
                        "type": "streamable-http",
                        "url": "http://localhost:3001/mcp",
                    },
                },
            ],
        }
    )

    assert package["identifier"] == "@example/weather-http"


def test_build_stdio_gateway_command_uses_one_pinned_dependency_tree():
    # stdio has no network endpoint of its own. Supergateway turns it into the
    # fixed local /mcp endpoint consumed by Traefik and the registration task.
    script, environment = build_stdio_gateway_command(
        {
            "registryType": "npm",
            "identifier": "@example/stdio-mcp",
            "version": "1.2.3",
            "transport": {"type": "stdio"},
            "packageArguments": [
                {"type": "named", "name": "--mode", "value": "safe mode"}
            ],
            "environmentVariables": [{"name": "LOG_LEVEL", "default": "info"}],
        },
        "supergateway@3.4.3",
    )

    # Installing both exact versions before startup prevents two nested npx
    # processes from racing over incomplete temporary node_modules directories.
    assert "supergateway@3.4.3 @example/stdio-mcp@1.2.3" in script
    assert "npx --offline -y @example/stdio-mcp@1.2.3" in script
    assert "--mode=safe mode" in script
    assert "--outputTransport streamableHttp" in script
    assert "--port 8000 --streamableHttpPath /mcp" in script
    assert environment == {"LOG_LEVEL": "info"}


def test_build_npm_command_resolves_registry_arguments_and_environment():
    # This covers a package that provides all required startup values in Registry
    # metadata, so no secret or arbitrary runtime value is needed from the caller.
    command, environment = build_npm_mcp_command(
        {
            "registryType": "npm",
            "identifier": "@example/weather-mcp",
            "version": "2.1.0",
            "transport": {
                "type": "streamable-http",
                "url": "http://localhost:{PORT}/mcp",
            },
            "runtimeArguments": [
                {"type": "named", "name": "--log-level", "value": "info"}
            ],
            "packageArguments": [{"type": "positional", "value": "http"}],
            "environmentVariables": [
                {"name": "WEATHER_UNITS", "default": "metric"},
                {"name": "PORT", "default": "3001"},
            ],
        }
    )

    # Nomad runs npx directly. Every Registry argument remains a distinct argv
    # entry, and the immutable package reference appears exactly once.
    assert command == [
        "-y",
        "--log-level=info",
        "@example/weather-mcp@2.1.0",
        "http",
    ]
    assert environment == {"WEATHER_UNITS": "metric", "PORT": "3001"}

    # The declared local transport determines the Nomad target port and the path
    # that Traefik and LiteLLM will expose outside the allocation.
    assert resolve_streamable_http_package_transport(
        {
            "transport": {
                "type": "streamable-http",
                "url": "http://localhost:{PORT}/mcp",
            }
        },
        environment,
    ) == (3001, "/mcp")


def test_build_npm_command_rejects_missing_required_input():
    # Current API requests contain only name and VO. A package requiring a token
    # must therefore fail before a Nomad job is created, rather than start broken.
    with pytest.raises(MCPPackageNotDeployableError, match="API_TOKEN"):
        build_npm_mcp_command(
            {
                "registryType": "npm",
                "identifier": "@example/private-mcp",
                "version": "1.0.0",
                "transport": {
                    "type": "streamable-http",
                    "url": "http://localhost:3001/mcp",
                },
                "environmentVariables": [{"name": "API_TOKEN", "isRequired": True}],
            }
        )


def test_build_npm_command_replaces_unpinned_registry_reference():
    # Even if publisher metadata asks npx for another tag, deployment must execute
    # the exact Registry version selected by PAPI and installed in the allocation.
    command, _ = build_npm_mcp_command(
        {
            "registryType": "npm",
            "identifier": "@example/weather-mcp",
            "version": "2.1.0",
            "runtimeArguments": [
                {
                    "type": "positional",
                    "value": "@example/weather-mcp@latest",
                }
            ],
        }
    )

    assert command == ["-y", "@example/weather-mcp@2.1.0"]


def test_streamable_http_transport_resolves_argument_value_hint():
    # Registry URLs may reference an argument's valueHint rather than an
    # environment variable. That same resolved port must configure Nomad routing.
    package = {
        "transport": {
            "type": "streamable-http",
            "url": "http://localhost:{http_port}/api/mcp",
        },
        "packageArguments": [
            {
                "type": "named",
                "name": "--port",
                "valueHint": "http_port",
                "default": "4100",
            }
        ],
    }

    assert resolve_streamable_http_package_transport(package, {}) == (
        4100,
        "/api/mcp",
    )


def test_streamable_http_transport_rejects_non_local_url():
    # A package entry can describe an already-hosted URL, but launching that npm
    # package in Nomad is valid only when it declares a local server endpoint.
    package = {
        "transport": {
            "type": "streamable-http",
            "url": "https://provider.example/mcp",
        }
    }

    with pytest.raises(MCPPackageNotDeployableError, match="local http://"):
        resolve_streamable_http_package_transport(package, {})


def test_create_mcp_deployment_builds_private_nomad_job(monkeypatch):
    # Parse is mocked because HCL parsing normally happens in the Nomad API. Its
    # result mirrors only the fields that MCPNomadClient enriches before submit.
    parsed_job = {
        "Meta": {
            "deployment_type": "self_deployed",
            "base_domain": "deployments.cloud.ai4eosc.eu",
        },
        "TaskGroups": [
            {
                "Tasks": [
                    {
                        "Name": "main",
                        "Config": {"args": []},
                        "Env": {"HOST": "0.0.0.0", "PORT": "3001"},
                    },
                    {
                        "Name": "register_mcp_in_litellm",
                        "Env": {
                            "LOCAL_MCP_URL": "http://${NOMAD_ADDR_mcp}/mcp",
                            "PUBLIC_MCP_URL": "https://mcp.example/mcp",
                        },
                        "Templates": [{"EmbeddedTmpl": "placeholder"}],
                    },
                    {
                        "Name": "deregister_mcp_from_litellm",
                        # This is how Nomad parses a task without an env block.
                        "Env": None,
                        "Templates": [{"EmbeddedTmpl": "placeholder"}],
                    },
                ]
            }
        ],
    }
    nomad = Mock()
    nomad.jobs.parse.return_value = parsed_job
    template = Template(
        'job "${JOB_ID}" { meta { auth = "${BASIC_AUTH_HASH}" } '
        'constraint { attribute = "$${meta.domain}" } '
        "resources { cpu = ${CPU_CORES} ram = ${MEMORY_MB} disk = ${DISK_MB} } }"
    )
    monkeypatch.setattr("ai4papi.mcp.nomad.uuid.uuid4", lambda: "fixed-id")

    client = MCPNomadClient(nomad_client=nomad, template=template)
    registry_server = {
        "name": "com.example/weather",
        "title": "Weather MCP",
        "description": "Weather tools",
        "version": "2.1.0",
    }
    package = {
        "registryType": "npm",
        "identifier": "@example/weather-mcp",
        "version": "2.1.0",
        "transport": {
            "type": "streamable-http",
            "url": "http://localhost:3001/mcp",
        },
    }

    result = client.create_mcp_deployment(
        registry_server=registry_server,
        package=package,
        owner="keycloak-user-id",
        namespace="ai4eosc",
        base_domain="deployments.cloud.ai4eosc.eu",
        litellm_url="https://litellm.example/",
        litellm_api_key="central-secret",
        resources={"cpu_num": 2, "ram": 2048, "disk": 4096},
    )

    assert result["job_id"] == "mcp-fixed-id"
    assert result["namespace"] == "ai4eosc"
    assert result["status"] == "submitted"
    assert result["endpoint"] is None

    # Owner, Registry identity and package identity become durable Nomad metadata;
    # listing and deletion can work without querying the Registry a second time.
    submitted = nomad.jobs.register_job.call_args.args[0]["Job"]
    assert submitted["Meta"] == {
        "deployment_type": "self_deployed",
        "base_domain": "deployments.cloud.ai4eosc.eu",
        "owner": "keycloak-user-id",
        "title": "Weather MCP",
        "description": "Weather tools",
        "registry_name": "com.example/weather",
        "registry_version": "2.1.0",
        "package_registry_type": "npm",
        "package_identifier": "@example/weather-mcp",
        "package_version": "2.1.0",
        "package_transport": "streamable-http",
        "exposed_transport": "streamable-http",
        "cpu_num": "2",
        "ram": "2048",
        "disk": "4096",
        "endpoint_path": "/mcp",
    }

    # The package implements Streamable HTTP itself, so Nomad executes its pinned
    # npm reference directly and does not add Supergateway or any stdio adapter.
    tasks = {task["Name"]: task for task in submitted["TaskGroups"][0]["Tasks"]}
    task_args = tasks["main"]["Config"]["args"]
    assert task_args == ["-y", "@example/weather-mcp@2.1.0"]
    assert "supergateway" not in " ".join(task_args)
    task_environment = tasks["main"]["Env"]
    assert task_environment["HOST"] == "0.0.0.0"
    assert task_environment["PORT"] == "3001"

    # The poststart task has everything needed to register independently from
    # PAPI, including durable Nomad metadata and the user's stable group name.
    register_environment = tasks["register_mcp_in_litellm"]["Env"]
    assert register_environment["CENTRAL_LITELLM_URL"] == "https://litellm.example"
    assert register_environment["CENTRAL_LITELLM_API_KEY"] == "central-secret"
    assert register_environment["MCP_OWNER"] == "keycloak-user-id"
    assert register_environment["MCP_REGISTRY_NAME"] == "com.example/weather"
    assert register_environment["MCP_PACKAGE_TRANSPORT"] == "streamable-http"
    assert register_environment["MCP_AUTHORIZATION_HEADER"].startswith("Basic ")
    assert (
        "nomad_job_id"
        in tasks["register_mcp_in_litellm"]["Templates"][0]["EmbeddedTmpl"]
    )

    # The poststop task shares the identity required to find and remove exactly
    # this job's registration, but the credential is not returned to the caller.
    deregister_environment = tasks["deregister_mcp_from_litellm"]["Env"]
    assert deregister_environment == {
        "CENTRAL_LITELLM_URL": "https://litellm.example",
        "CENTRAL_LITELLM_API_KEY": "central-secret",
        "MCP_ACCESS_GROUP_NAME": register_environment["MCP_ACCESS_GROUP_NAME"],
    }
    assert "basic_auth_header" not in result

    # Traefik receives only the password verifier; the generated Basic header is
    # passed directly to the registration task after HCL parsing.
    parsed_hcl = nomad.jobs.parse.call_args.args[0]
    assert "{SHA}" in parsed_hcl
    assert "resources { cpu = 2 ram = 2048 disk = 4096 }" in parsed_hcl
    assert register_environment["MCP_AUTHORIZATION_HEADER"] not in parsed_hcl


def test_create_stdio_mcp_deployment_adds_streamable_http_gateway(monkeypatch):
    # The parsed template contains the same three lifecycle tasks as the real HCL;
    # this test focuses on the stdio-specific command, port and durable metadata.
    parsed_job = {
        "Meta": {
            "deployment_type": "self_deployed",
            "base_domain": "deployments.cloud.ai4eosc.eu",
        },
        "TaskGroups": [
            {
                "Tasks": [
                    {"Name": "main", "Config": {"args": []}, "Env": {}},
                    {
                        "Name": "register_mcp_in_litellm",
                        "Env": {},
                        "Templates": [{"EmbeddedTmpl": "placeholder"}],
                    },
                    {
                        "Name": "deregister_mcp_from_litellm",
                        "Env": {},
                        "Templates": [{"EmbeddedTmpl": "placeholder"}],
                    },
                ]
            }
        ],
    }
    nomad = Mock()
    nomad.jobs.parse.return_value = parsed_job
    template = Template(
        'job "${JOB_ID}" { port = ${MCP_PORT} path = "${ENDPOINT_PATH}" }'
    )
    monkeypatch.setattr("ai4papi.mcp.nomad.uuid.uuid4", lambda: "stdio-id")

    client = MCPNomadClient(nomad_client=nomad, template=template)
    client.create_mcp_deployment(
        registry_server={
            "name": "ai.agentutility/mcp-web-probe",
            "description": "Web probe",
            "version": "0.32.1",
        },
        package={
            "registryType": "npm",
            "identifier": "@agentutility/mcp-web-probe",
            "version": "0.32.1",
            "transport": {"type": "stdio"},
        },
        owner="user-id",
        namespace="ai4eosc",
        base_domain="deployments.cloud.ai4eosc.eu",
        litellm_url="https://litellm.example",
        litellm_api_key="secret",
        resources={"cpu_num": 1, "ram": 1024, "disk": 2048},
    )

    # Nomad exposes port 8000 and /mcp although the original Registry package has
    # no HTTP endpoint. The main task bootstraps the adapter in the Node container.
    parsed_hcl = nomad.jobs.parse.call_args.args[0]
    assert "port = 8000" in parsed_hcl
    assert 'path = "/mcp"' in parsed_hcl
    submitted = nomad.jobs.register_job.call_args.args[0]["Job"]
    tasks = {task["Name"]: task for task in submitted["TaskGroups"][0]["Tasks"]}
    assert tasks["main"]["Config"]["command"] == "/bin/sh"
    assert tasks["main"]["Config"]["args"][0] == "-c"
    assert "supergateway@3.4.3" in tasks["main"]["Config"]["args"][1]
    assert submitted["Meta"]["package_transport"] == "stdio"
    assert submitted["Meta"]["exposed_transport"] == "streamable-http"
    assert tasks["register_mcp_in_litellm"]["Env"]["MCP_PACKAGE_TRANSPORT"] == "stdio"


def test_get_mcp_deployment_reports_nomad_health_and_registration_state():
    # A successful poststart task means the native endpoint answered initialize
    # and the corresponding LiteLLM registration and access group were persisted.
    nomad = Mock()
    nomad.job.get_job.return_value = {
        "Status": "running",
        "Meta": {
            "deployment_type": "self_deployed",
            "owner": "user-id",
            "base_domain": "deployments.example",
            "endpoint_path": "/mcp",
            "cpu_num": "2",
            "ram": "2048",
            "disk": "4096",
        },
    }
    nomad.job.get_allocations.return_value = [
        {"ID": "allocation-id", "ClientStatus": "running", "CreateTime": 1}
    ]
    nomad.allocation.get_allocation.return_value = {
        "NodeID": "node-id",
        "DeploymentStatus": {"Healthy": True},
        "TaskStates": {
            "main": {"State": "running", "Failed": False},
            "register_mcp_in_litellm": {"State": "dead", "Failed": False},
        },
    }
    nomad.node.get_node.return_value = {"Meta": {"domain": "node"}}

    result = MCPNomadClient(
        nomad_client=nomad, template=Template("")
    ).get_mcp_deployment("mcp-job", "ai4eosc", owner="user-id")

    assert result["status"] == "running"
    assert result["healthy"] is True
    assert result["registration_status"] == "registered"
    assert result["endpoint"] == "https://mcp-job.node-deployments.example/mcp"
    assert result["resources"] == {"cpu_num": 2, "ram": 2048, "disk": 4096}
