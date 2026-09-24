"""Manage the lifecycle of PAPI-owned MCP deployments in Nomad."""

from datetime import datetime
from typing import Any
import uuid

from nomad.api import exceptions as nomad_exceptions

import ai4papi.conf as papiconf
from ai4papi.litellm_client import (
    build_litellm_mcp_access_group_name,
    build_litellm_mcp_server_name,
)
from ai4papi.mcp.packages import (
    MCPPackageNotDeployableError,
    build_npm_mcp_command,
)
from ai4papi.mcp.transports import (
    STDIO_GATEWAY_PATH,
    STDIO_GATEWAY_PORT,
    build_stdio_gateway_command,
    create_mcp_basic_auth,
    resolve_streamable_http_package_transport,
)
import ai4papi.nomad_utils as nomad_utils


class MCPNomadError(RuntimeError):
    """A self-deployed MCP could not be managed in Nomad."""


class MCPNomadDeploymentNotFoundError(MCPNomadError):
    """The requested MCP job does not exist in its recorded namespace."""


class MCPNomadClient:
    """Small testable client for the lifecycle of PAPI-managed MCP jobs."""

    def __init__(
        self,
        *,
        nomad_client=None,
        template=None,
    ) -> None:
        self.nomad = nomad_client or nomad_utils.Nomad
        self.template = template or papiconf.MCP["nomad"]

    def create_mcp_deployment(
        self,
        *,
        registry_server: dict[str, Any],
        package: dict[str, Any],
        owner: str,
        namespace: str,
        base_domain: str,
        litellm_url: str,
        litellm_api_key: str,
        resources: dict[str, int],
        priority: int = 50,
    ) -> dict[str, Any]:
        """Submit an HTTP or stdio MCP job that manages its LiteLLM registration."""

        command_arguments, package_environment = build_npm_mcp_command(package)
        package_transport = (package.get("transport") or {}).get("type")

        if package_transport == "streamable-http":
            package_port, endpoint_path = resolve_streamable_http_package_transport(
                package,
                package_environment,
            )
            main_command = "npx"
            main_arguments = command_arguments

        # stdio is supported through an aditional adapter.
        elif package_transport == "stdio":
            package_port = STDIO_GATEWAY_PORT
            endpoint_path = STDIO_GATEWAY_PATH
            gateway_script, package_environment = build_stdio_gateway_command(
                package,
                papiconf.MCP_NOMAD_SUPERGATEWAY_PACKAGE,
            )
            main_command = "/bin/sh"
            main_arguments = ["-c", gateway_script]
        else:
            raise MCPPackageNotDeployableError(
                "The selected npm package must use Streamable HTTP or stdio."
            )

        # Basic auth is added through Traefik labels.
        # Then, litellm authenticates using static headers
        basic_auth = create_mcp_basic_auth()
        job_id = f"mcp-{uuid.uuid4()}"

        raw_job = self.template.safe_substitute(
            {
                "JOB_ID": job_id,
                "NAMESPACE": namespace,
                "PRIORITY": priority,
                "BASE_DOMAIN": base_domain,
                "BASIC_AUTH_HASH": basic_auth.password_hash,
                "NODE_IMAGE": papiconf.MCP_NOMAD_NODE_IMAGE,
                "CPU_CORES": resources["cpu_num"],
                "MEMORY_MB": resources["ram"],
                "DISK_MB": resources["disk"],
                "MCP_PORT": package_port,
                "ENDPOINT_PATH": endpoint_path,
            }
        )
        job = self.nomad.jobs.parse(raw_job)

        job["Meta"].update(
            {
                "owner": owner,
                "title": registry_server.get("title") or registry_server["name"],
                "description": registry_server.get("description") or "",
                "registry_name": registry_server["name"],
                "registry_version": registry_server["version"],
                "package_registry_type": package["registryType"],
                "package_identifier": package["identifier"],
                "package_version": package["version"],
                "endpoint_path": endpoint_path,
                "package_transport": package_transport,
                "exposed_transport": "streamable-http",
                "cpu_num": str(resources["cpu_num"]),
                "ram": str(resources["ram"]),
                "disk": str(resources["disk"]),
            }
        )

        tasks = {task["Name"]: task for task in job["TaskGroups"][0]["Tasks"]}
        main_task = tasks["main"]
        # Native HTTP packages run directly. A stdio package runs behind the local
        # adapter built above; in both cases LiteLLM sees the same HTTP endpoint.
        main_task["Config"]["command"] = main_command
        main_task["Config"]["args"] = main_arguments
        main_task["Env"].update(package_environment)
        # These conventional variables align the process with Nomad's network.
        # Packages using other variable names must declare them in Registry data.
        main_task["Env"]["HOST"] = "0.0.0.0"
        main_task["Env"]["PORT"] = str(package_port)

        # Registration is performed inside the allocation. PAPI returns as soon as
        # Nomad accepts the job instead of waiting for scheduling, Traefik and MCP
        # startup before issuing several synchronous LiteLLM management requests.
        shared_litellm_environment = {
            "CENTRAL_LITELLM_URL": litellm_url.rstrip("/"),
            "CENTRAL_LITELLM_API_KEY": litellm_api_key,
            "MCP_OWNER": owner,
            "MCP_ACCESS_GROUP_NAME": build_litellm_mcp_access_group_name(owner),
        }
        register_task = tasks["register_mcp_in_litellm"]
        # Nomad's HCL parser returns ``Env=None`` when a task has no env block.
        # Normalize both representations before adding lifecycle configuration.
        register_task["Env"] = register_task.get("Env") or {}
        register_task["Env"].update(
            {
                **shared_litellm_environment,
                "MCP_AUTHORIZATION_HEADER": basic_auth.authorization_header,
                "MCP_SERVER_NAME": build_litellm_mcp_server_name(
                    registry_server["name"], owner
                ),
                "MCP_DESCRIPTION": registry_server.get("description") or "",
                "MCP_REGISTRY_NAME": registry_server["name"],
                "MCP_REGISTRY_VERSION": registry_server["version"],
                "MCP_PACKAGE_REGISTRY_TYPE": package["registryType"],
                "MCP_PACKAGE_IDENTIFIER": package["identifier"],
                "MCP_PACKAGE_VERSION": package["version"],
                "MCP_PACKAGE_TRANSPORT": package_transport,
                "MCP_STARTUP_TIMEOUT": str(papiconf.MCP_NOMAD_STARTUP_TIMEOUT),
                "MCP_POLL_INTERVAL": str(papiconf.MCP_NOMAD_POLL_INTERVAL),
            }
        )
        register_task["Templates"][0]["EmbeddedTmpl"] = papiconf.MCP["register"]

        deregister_task = tasks["deregister_mcp_from_litellm"]
        deregister_task["Env"] = deregister_task.get("Env") or {}
        deregister_task["Env"].update(
            {
                "CENTRAL_LITELLM_URL": shared_litellm_environment[
                    "CENTRAL_LITELLM_URL"
                ],
                "CENTRAL_LITELLM_API_KEY": litellm_api_key,
                "MCP_ACCESS_GROUP_NAME": shared_litellm_environment[
                    "MCP_ACCESS_GROUP_NAME"
                ],
            }
        )
        deregister_task["Templates"][0]["EmbeddedTmpl"] = papiconf.MCP["deregister"]

        try:
            self.nomad.jobs.register_job({"Job": job})
        except Exception as exc:
            raise MCPNomadError(
                f"Unable to submit the MCP job to Nomad: {exc}"
            ) from exc

        return {
            "job_id": job_id,
            "namespace": namespace,
            "status": "submitted",
            "endpoint": None,
        }

    def get_mcp_deployment(
        self,
        job_id: str,
        namespace: str,
        *,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """Return an MCP-specific view of a Nomad job and its latest allocation."""

        try:
            job = self.nomad.job.get_job(id_=job_id, namespace=namespace)
        except nomad_exceptions.URLNotFoundNomadException as exc:
            raise MCPNomadDeploymentNotFoundError(
                f'No MCP deployment named "{job_id}" exists in namespace "{namespace}".'
            ) from exc

        metadata = job.get("Meta") or {}
        if (
            not job_id.startswith("mcp-")
            or metadata.get("deployment_type") != "self_deployed"
        ):
            raise MCPNomadError(
                f'The Nomad job "{job_id}" is not a PAPI MCP deployment.'
            )
        if owner is not None and metadata.get("owner") != owner:
            raise MCPNomadError("You are not the owner of that MCP deployment.")

        status = (
            "queued" if job.get("Status") == "pending" else job.get("Status", "unknown")
        )
        endpoint = None
        error_message = None
        allocation_id = None
        healthy = False

        allocations = self.nomad.job.get_allocations(id_=job_id, namespace=namespace)
        if allocations:
            ordered = sorted(
                allocations,
                key=lambda allocation: allocation.get("CreateTime", 0),
                reverse=True,
            )
            selected = next(
                (
                    allocation
                    for allocation in ordered
                    if allocation.get("ClientStatus") == "running"
                ),
                ordered[0],
            )
            allocation_id = selected["ID"]
            allocation = self.nomad.allocation.get_allocation(allocation_id)
            task_states = allocation.get("TaskStates") or {}
            task_state = task_states.get("main") or {}
            status = {
                "pending": "starting",
                "unknown": "down",
            }.get(task_state.get("State"), task_state.get("State", status))

            # The poststart task succeeds only after the MCP answers initialize and
            # the LiteLLM server/group metadata are stored. Expose that distinction
            # instead of reporting a merely running container as fully registered.
            registration_state = task_states.get("register_mcp_in_litellm") or {}
            registration_status = registration_state.get("State", "pending")
            registration_succeeded = (
                registration_status == "dead"
                and registration_state.get("Failed") is not True
            )
            if registration_state.get("Failed"):
                status = "registration_failed"
            elif registration_state.get("State") == "running":
                status = "registering"
            elif registration_succeeded:
                registration_status = "registered"

            deployment_status = allocation.get("DeploymentStatus") or {}
            healthy = (
                deployment_status.get("Healthy") is True
                and task_state.get("State") == "running"
                and registration_succeeded
            )

            events = (
                registration_state.get("Events")
                if status == "registration_failed"
                else task_state.get("Events")
            ) or []
            if status in {"failed", "registration_failed"} and events:
                error_message = events[0].get("Message")

            node_id = allocation.get("NodeID")
            if node_id:
                node = self.nomad.node.get_node(node_id)
                node_domain = (node.get("Meta") or {}).get("domain")
                base_domain = metadata.get("base_domain")
                if node_domain and base_domain:
                    endpoint_path = metadata.get("endpoint_path") or "/"
                    endpoint = (
                        f"https://{job_id}.{node_domain}-{base_domain}{endpoint_path}"
                    )

        submitted_at = None
        if job.get("SubmitTime"):
            submitted_at = datetime.fromtimestamp(
                job["SubmitTime"] // 1_000_000_000
            ).isoformat()

        return {
            "job_id": job_id,
            "namespace": namespace,
            "status": status,
            "endpoint": endpoint,
            "allocation_id": allocation_id,
            "healthy": healthy if allocation_id is not None else False,
            "registration_status": (
                registration_status if allocation_id is not None else "pending"
            ),
            "error_message": error_message,
            "submitted_at": submitted_at,
            "registry_name": metadata.get("registry_name"),
            "registry_version": metadata.get("registry_version"),
            "package": {
                "registry_type": metadata.get("package_registry_type"),
                "identifier": metadata.get("package_identifier"),
                "version": metadata.get("package_version"),
                "transport": metadata.get("package_transport"),
            },
            "resources": {
                "cpu_num": int(metadata["cpu_num"]),
                "ram": int(metadata["ram"]),
                "disk": int(metadata["disk"]),
            },
        }

    def delete_mcp_deployment(
        self,
        *,
        job_id: str,
        namespace: str,
        owner: str,
        ignore_not_found: bool = False,
    ) -> None:
        """Purge a PAPI MCP job after checking its recorded owner."""

        try:
            self.get_mcp_deployment(job_id, namespace, owner=owner)
        except MCPNomadDeploymentNotFoundError:
            if ignore_not_found:
                return
            raise

        try:
            self.nomad.job.deregister_job(
                id_=job_id,
                namespace=namespace,
                purge=True,
            )
        except nomad_exceptions.URLNotFoundNomadException:
            if ignore_not_found:
                return
            raise MCPNomadDeploymentNotFoundError(
                f'No MCP deployment named "{job_id}" exists in namespace "{namespace}".'
            )
        except Exception as exc:
            raise MCPNomadError(
                f"Unable to delete the MCP job from Nomad: {exc}"
            ) from exc
