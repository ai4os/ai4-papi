"""Manage external MCP servers exposed through LiteLLM."""

from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
import requests

from ai4papi import auth, quotas, utils
import ai4papi.conf as papiconf
from ai4papi.litellm_client import LiteLLMClient
from ai4papi.mcp.nomad import (
    MCPNomadClient,
    MCPNomadDeploymentNotFoundError,
    MCPNomadError,
)
from ai4papi.mcp.packages import (
    MCPPackageNotDeployableError,
    select_npm_mcp_package,
)
from ai4papi.mcp.registry import (
    MCPRegistryClient,
    MCPRegistryError,
    MCPRegistryTimeoutError,
    MCPRegistryUnavailableError,
    MCPServerNotFoundError,
    select_remote_endpoint,
)

router = APIRouter(
    prefix="/mcp",
    tags=["AI4OS LLM (MCP)"],
    responses={404: {"description": "Not found"}},
)

security = HTTPBearer()


class MCPCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        description="Exact name in the official MCP registry",
    )
    vo: str | None = Field(
        default=None,
        description=(
            "VO used for a self-deployed MCP. Remote MCPs ignore this field; "
            "deployable MCPs use MCP_NOMAD_DEFAULT_VO when it is omitted."
        ),
    )
    conf: dict | None = Field(
        default=None,
        description=(
            "Partial self-deployment configuration. Missing hardware values use "
            "the defaults from etc/mcp/user.yaml; remote MCPs ignore this field."
        ),
    )


def get_registry_client() -> MCPRegistryClient:
    return MCPRegistryClient()


def get_litellm_client() -> LiteLLMClient:
    return LiteLLMClient(
        base_url=papiconf.LITELLM_URL,
        api_key=papiconf.LITELLM_API_KEY,
        timeout=papiconf.LITELLM_TIMEOUT,
    )


def get_mcp_nomad_client() -> MCPNomadClient:
    return MCPNomadClient()


def _authorized_mcp_namespaces(auth_info: dict) -> list[str]:
    """Return Nomad namespaces belonging to the user's Keycloak VOs."""

    user_vos = {
        vo
        for vos in auth_info.get("groups", {}).values()
        for vo in vos
        if vo in papiconf.MAIN_CONF["nomad"]["namespaces"]
    }
    return [papiconf.MAIN_CONF["nomad"]["namespaces"][vo] for vo in user_vos]


def _redact_mcp_registration(server: dict) -> dict:
    """Remove upstream credentials from LiteLLM admin responses."""

    registration = deepcopy(server)
    static_headers = registration.get("static_headers")
    if isinstance(static_headers, dict):
        registration["static_headers"] = {name: "***" for name in static_headers}
    if registration.get("credentials"):
        registration["credentials"] = "***"
    return registration


def _format_mcp_list(
    servers: list[dict],
    deployments_by_id: dict[str, dict],
) -> list[dict]:
    """
    Attach Nomad state only to self-deployed LiteLLM registrations.
    It takes the list of LiteLLM visible mcp servers and the dict of Nomad deployments keyed by job_id:
    """

    result = []

    for server in servers:
        mcp_info = server.get("mcp_info") or {}
        if not isinstance(mcp_info, dict):
            mcp_info = {}
        job_id = mcp_info.get("nomad_job_id")
        deployment = deployments_by_id.get(job_id)

        result.append(
            {
                "name": mcp_info.get("registry_name") or server.get("server_name"),
                "version": mcp_info.get("registry_version"),
                "deployment_type": mcp_info.get("deployment_type") or "remote",
                "endpoint": {
                    "transport": server.get("transport"),
                    "url": server.get("url"),
                },
                "registration": _redact_mcp_registration(server),
                "deployment": deployment,
            }
        )

    return result


def _rollback_mcp_creation(
    *,
    litellm: LiteLLMClient,
    owner: str,
    access_was_granted: bool,
    server_id: str | None,
) -> None:
    """Best-effort compensation for a partial remote MCP registration."""

    # Undo mutations in reverse order: permission -> server. Only
    # this server is removed; the user's stable group and their other MCPs remain.
    if access_was_granted and server_id is not None:
        try:
            litellm.remove_mcp_server_from_user_access_group(
                owner=owner,
                server_id=server_id,
            )
        except requests.RequestException:
            pass

    if server_id is not None:
        try:
            litellm.delete_mcp_server(server_id)
        except requests.RequestException:
            pass


@router.get("")
def list_mcps(
    authorization=Depends(security),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    nomad: MCPNomadClient = Depends(get_mcp_nomad_client),
):
    """List visible LiteLLM MCPs, enriching self-deployed ones from Nomad."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    try:
        servers = litellm.list_user_accessible_mcp_servers(auth_info["id"])
        deployments_by_id = {}
        for server in servers:
            mcp_info = server.get("mcp_info") or {}
            if not isinstance(mcp_info, dict):
                continue
            if mcp_info.get("deployment_type") != "self_deployed":
                continue

            # LiteLLM is the catalogue and source of authorization. Nomad is only
            # queried for the concrete job referenced by an accessible MCP; PAPI
            # never enumerates a namespace looking for additional MCP jobs.
            job_id = mcp_info.get("nomad_job_id")
            namespace = mcp_info.get("nomad_namespace")
            if not job_id or not namespace:
                continue
            try:
                deployments_by_id[job_id] = nomad.get_mcp_deployment(
                    job_id,
                    namespace,
                )
            except MCPNomadDeploymentNotFoundError:
                deployments_by_id[job_id] = {
                    "job_id": job_id,
                    "namespace": namespace,
                    "status": "missing",
                    "healthy": False,
                    "endpoint": None,
                    "error_message": "The referenced Nomad job no longer exists.",
                }
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MCPNomadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _format_mcp_list(servers, deployments_by_id)


@router.post("", status_code=201)
def create_mcp(
    request: MCPCreateRequest,
    authorization=Depends(security),
    registry: MCPRegistryClient = Depends(get_registry_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    nomad: MCPNomadClient = Depends(get_mcp_nomad_client),
):
    """Register a remote MCP or deploy its npm package in Nomad."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    server_id = None
    access_was_granted = False
    try:
        # Registry lookup and endpoint selection are read-only. LiteLLM is mutated
        # only after the requested name resolves to a supported remote or package.
        registry_server = registry.get_server(request.name)

        remote_endpoint = None
        if registry_server.get("remotes"):
            try:
                remote_endpoint = select_remote_endpoint(registry_server)
            except MCPRegistryError:
                # A hybrid entry may expose only SSE remotely but also publish a
                # supported npm package. It can still be deployed even though the
                # provider endpoint itself is unsupported.
                if not registry_server.get("packages"):
                    raise

        if remote_endpoint is not None:
            # Remote and hybrid entries prefer a provider-hosted Streamable HTTP
            # endpoint, avoiding an unnecessary Nomad deployment.
            deployment_type = "remote"
            deployment = None
            endpoint = remote_endpoint
            registration = litellm.create_remote_mcp_server(
                registry_server=registry_server,
                owner=auth_info["id"],
                endpoint=endpoint,
            )
        else:
            # Deployable case: validate the VO before consuming cluster resources.
            vo = request.vo or papiconf.MCP_NOMAD_DEFAULT_VO
            if vo not in papiconf.MAIN_CONF["nomad"]["namespaces"]:
                raise HTTPException(status_code=422, detail=f'Unknown VO "{vo}".')
            auth.check_authorization(auth_info, vo)

            package = select_npm_mcp_package(registry_server)
            namespace = papiconf.MAIN_CONF["nomad"]["namespaces"][vo]
            base_domain = papiconf.MAIN_CONF["lb"]["domain"][vo]

            # Use the same configuration flow as catalog modules: start from the
            # declarative YAML defaults, merge only known submitted keys and enforce
            # the resource ranges before creating any Nomad resource.
            user_conf = deepcopy(papiconf.MCP["user"]["values"])
            if request.conf is not None:
                user_conf = utils.update_values_conf(
                    submitted=request.conf,
                    reference=user_conf,
                )
            user_conf = utils.validate_conf(user_conf)
            quotas.check_jobwise(conf=user_conf, vo=vo, item_name="mcp")

            submitted = nomad.create_mcp_deployment(
                registry_server=registry_server,
                package=package,
                owner=auth_info["id"],
                namespace=namespace,
                base_domain=base_domain,
                litellm_url=papiconf.LITELLM_URL,
                litellm_api_key=papiconf.LITELLM_API_KEY,
                resources=user_conf["hardware"],
            )

            # Nomad accepted the durable resource, so return immediately. Its
            # poststart task now waits for the MCP, registers it in LiteLLM with
            # Nomad metadata and updates the user's stable access group.
            return {
                "name": registry_server["name"],
                "version": registry_server["version"],
                "deployment_type": "self_deployed",
                "endpoint": None,
                "registration": None,
                "deployment": submitted,
            }

        server_id = registration["server_id"]

        # All of this user's MCPs share one stable private access group. Existing
        # keys point to that group; the shared authorization team is never changed.
        key_ids = litellm.list_user_mcp_key_ids(auth_info["id"])
        access_group = litellm.add_mcp_server_to_user_access_group(
            owner=auth_info["id"],
            server_id=server_id,
            key_ids=key_ids,
        )
        access_was_granted = True

        # Persist the group link while retaining the remote Registry metadata. Add user access group id to the MCP info.
        mcp_info = dict(registration.get("mcp_info") or {})
        mcp_info.update(
            {
                "access_group_id": access_group["access_group_id"],
                "access_group_name": access_group["access_group_name"],
            }
        )
        registration = litellm.update_mcp_server_metadata(server_id, mcp_info)

    except Exception as exc:
        # Remote creation spans Registry, LiteLLM and its access group. Compensate
        # every completed mutation so a later retry starts from a clean state.
        _rollback_mcp_creation(
            litellm=litellm,
            owner=auth_info["id"],
            access_was_granted=access_was_granted,
            server_id=server_id,
        )

        # Convert errors from each backend into the same HTTP semantics used by
        # the existing remote MCP flow, after cleanup has been attempted.
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, MCPServerNotFoundError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, MCPRegistryTimeoutError):
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        if isinstance(exc, MCPRegistryUnavailableError):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if isinstance(exc, MCPRegistryError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, MCPPackageNotDeployableError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, MCPNomadError):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if isinstance(exc, requests.HTTPError):
            status_code = exc.response.status_code if exc.response is not None else 502
            detail = exc.response.text if exc.response is not None else str(exc)
            raise HTTPException(status_code=status_code, detail=detail) from exc
        if isinstance(exc, (requests.RequestException, ValueError, KeyError)):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise

    response_registration = _redact_mcp_registration(registration)

    return {
        "name": registry_server["name"],
        "version": registry_server["version"],
        "deployment_type": deployment_type,
        "endpoint": endpoint,
        "registration": response_registration,
        "deployment": deployment,
    }


@router.delete("/{server_id}", status_code=204)
def delete_mcp(
    server_id: str,
    authorization=Depends(security),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    nomad: MCPNomadClient = Depends(get_mcp_nomad_client),
):
    """Delete an MCP server after verifying ownership in LiteLLM metadata."""
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info)

    try:
        server = litellm.get_mcp_server(server_id)
        mcp_info = server.get("mcp_info") or {}
        owner = mcp_info.get("owner")

        # The admin key used internally could delete any server. Enforce ownership
        # in PAPI before making that LiteLLM request.
        if owner != auth_info["id"]:
            raise HTTPException(
                status_code=403,
                detail="You are not the owner of that MCP server.",
            )

        # Stop a self-deployed server before removing its LiteLLM registration, so
        # a partial failure cannot leave a reachable Nomad service without PAPI's
        # ownership record. A retry is safe when the job is already absent.
        if mcp_info.get("deployment_type") == "self_deployed":
            job_id = mcp_info.get("nomad_job_id")
            namespace = mcp_info.get("nomad_namespace")
            if not job_id or not namespace:
                raise HTTPException(
                    status_code=502,
                    detail="The self-deployed MCP has incomplete Nomad metadata.",
                )
            if namespace not in _authorized_mcp_namespaces(auth_info):
                raise HTTPException(
                    status_code=403,
                    detail="The MCP deployment is outside your authorized namespaces.",
                )
            nomad.delete_mcp_deployment(
                job_id=job_id,
                namespace=namespace,
                owner=auth_info["id"],
                ignore_not_found=True,
            )

        # Always complete LiteLLM cleanup synchronously. The job's poststop task is
        # only a fallback for jobs removed directly through Nomad; its operations
        # are idempotent if it races with this request.
        litellm.remove_mcp_server_from_user_access_group(
            owner=auth_info["id"], server_id=server_id
        )
        litellm.delete_mcp_server(server_id)

    except HTTPException:
        raise
    except MCPNomadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
