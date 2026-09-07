"""
Client for the LiteLLM management API.

It avoids direct HTTP calls to LiteLLM from the MCP router, and provides a convenient
interface for creating and managing remote MCP servers.

TODO Update keys.py to use this client instead of direct HTTP calls to LiteLLM.
"""

import hashlib
import re
from typing import Any

import requests


def build_litellm_mcp_server_name(registry_name: str, owner: str) -> str:
    """Build a globally unique LiteLLM/MCP-compatible server name."""

    readable_name = registry_name.rsplit("/", 1)[-1]
    readable_name = re.sub(r"[^A-Za-z0-9_-]", "_", readable_name).strip("_-")
    readable_name = readable_name[:40] or "server"
    digest = hashlib.sha256(f"{owner}:{registry_name}".encode()).hexdigest()[:12]

    return f"papi_{readable_name}_{digest}"


def build_litellm_mcp_access_group_name(owner: str, server_id: str) -> str:
    """Build a stable name for the private access group of an MCP server."""

    owner_digest = hashlib.sha256(owner.encode()).hexdigest()[:12]
    server_fragment = re.sub(r"[^A-Za-z0-9_-]", "_", server_id)[:16]
    return f"papi_mcp_{owner_digest}_{server_fragment}"


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        session: requests.Session | None = None,
    ) -> None:

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

    def create_remote_mcp_server(
        self,
        *,
        registry_server: dict[str, Any],
        owner: str,
        endpoint: dict[str, str],
    ) -> dict[str, Any]:
        server_name = build_litellm_mcp_server_name(registry_server["name"], owner)
        payload = {
            "server_name": server_name,
            "description": registry_server.get("description"),
            "transport": endpoint["transport"],
            "url": endpoint["url"],
            # This MCP is private: only explicitly granted keys can use it.
            "allow_all_keys": False,
            "mcp_info": {
                # owner is the Keycloak subject (sub), used by PAPI for authorization.
                "owner": owner,
                "source": "official-mcp-registry",
                "registry_name": registry_server["name"],
                "registry_version": registry_server["version"],
                "deployment_type": "remote",
                "nomad_job_id": None,
            },
        }
        response = self.session.post(
            f"{self.base_url}/v1/mcp/server",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_mcp_server(self, server_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/v1/mcp/server/{server_id}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """Return all MCP servers visible to PAPI's LiteLLM admin key."""
        response = self.session.get(
            f"{self.base_url}/v1/mcp/server",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def list_user_mcp_key_ids(self, user_id: str) -> list[str]:
        """Return the stored LiteLLM identifiers of all keys owned by a user."""

        response = self.session.get(
            f"{self.base_url}/key/list",
            params={"user_id": user_id, "return_full_object": "true"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [
            key["token"] for key in response.json().get("keys", []) if key.get("token")
        ]

    def create_mcp_access_group(
        self,
        *,
        owner: str,
        server_id: str,
        key_ids: list[str],
    ) -> dict[str, Any]:
        """Create a private MCP grant and assign it to existing user keys."""

        payload = {
            "access_group_name": build_litellm_mcp_access_group_name(owner, server_id),
            "description": f"Private PAPI access to MCP server {server_id}",
            "access_mcp_server_ids": [server_id],
            # Assigning the group directly to keys is intentional. Assigning it to
            # the user's shared authorization-level team (for example ``ap-d``)
            # would also expose the private MCP to every other member of that team.
            "assigned_key_ids": key_ids,
            "assigned_team_ids": [],
        }
        response = self.session.post(
            f"{self.base_url}/v1/access_group",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_mcp_access_group(self, access_group_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/v1/access_group/{access_group_id}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def delete_mcp_access_group(
        self,
        access_group_id: str,
        *,
        ignore_not_found: bool = False,
    ) -> None:
        response = self.session.delete(
            f"{self.base_url}/v1/access_group/{access_group_id}",
            timeout=self.timeout,
        )
        if ignore_not_found and response.status_code == 404:
            return
        response.raise_for_status()

    def update_mcp_server_metadata(
        self,
        server_id: str,
        mcp_info: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace only the MCP metadata field of an existing server."""

        response = self.session.put(
            f"{self.base_url}/v1/mcp/server",
            json={"server_id": server_id, "mcp_info": mcp_info},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def list_user_accessible_mcp_servers(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Return MCP servers available to a user through all supported grants."""
        servers = self.list_mcp_servers()

        # The admin key sees every MCP, so collect the user's key- and team-level
        # entitlements before filtering that complete list.

        # Get the user's team memberships and their object permissions.
        user_response = self.session.get(
            f"{self.base_url}/user/info",
            params={"user_id": user_id},
            timeout=self.timeout,
        )
        # A user can own an MCP before creating a LiteLLM key. In that case there
        # is no LiteLLM user row yet, so treat 404 as "no team memberships" and
        # continue: owned and public MCPs can still be listed without creating data.
        if user_response.status_code == 404:
            user = {"teams": []}
        else:
            user_response.raise_for_status()
            user = user_response.json()

        # Get the user's keys. For a user not present in LiteLLM this is normally
        # empty, but querying it keeps the same permission flow for every user.
        keys_response = self.session.get(
            f"{self.base_url}/key/list",
            params={"user_id": user_id, "return_full_object": "true"},
            timeout=self.timeout,
        )
        keys_response.raise_for_status()
        keys = keys_response.json().get("keys", [])

        # Case 1: a key can belong to a team even if the user information does not
        # include that membership. Collect the team attached to every user key.
        team_ids = {key.get("team_id") for key in keys if key.get("team_id")}

        # Case 2: the user may belong to a team such as "researchers" even when
        # none of their current keys uses that team. Include explicit memberships.
        team_ids.update(
            team.get("team_id") for team in user.get("teams", []) if team.get("team_id")
        )

        # Keys and teams can both carry permission data, so keep them in one list
        # and process all possible visibility sources below.
        permission_records = list(keys)
        for team_id in team_ids:
            # Fetching each team lets us discover grants inherited through team
            # membership, for example access to the MCP group "research".
            team_response = self.session.get(
                f"{self.base_url}/team/info",
                params={"team_id": team_id},
                timeout=self.timeout,
            )
            team_response.raise_for_status()
            permission_records.append(team_response.json())

        # Legacy permissions can grant a server ID directly or match a named MCP
        # group stored on the server. Unified access groups instead appear as
        # top-level ``access_group_ids`` on keys and teams.
        allowed_server_ids: set[str] = set()
        allowed_legacy_mcp_groups: set[str] = set()
        access_group_ids: set[str] = set()
        for record in permission_records:
            # Unified access-group case: when PAPI creates a private MCP it adds
            # the new group's ID directly to each existing key of its owner. The
            # group, rather than the shared team, names the permitted MCP server.
            access_group_ids.update(record.get("access_group_ids") or [])

            object_permission = record.get("object_permission") or {}
            # Old or externally managed records may contain null or another invalid
            # representation. Ignore them instead of treating them as permissions.
            if not isinstance(object_permission, dict):
                continue

            # Direct-grant case: a key or team explicitly names an MCP server ID,
            # for example mcp_servers=["server-1"].
            allowed_server_ids.update(object_permission.get("mcp_servers") or [])

            # Group-grant case: a key or team grants a group such as "research";
            # every MCP server assigned to that group becomes a candidate.
            allowed_legacy_mcp_groups.update(
                object_permission.get("mcp_access_groups") or []
            )

        for access_group_id in access_group_ids:
            try:
                access_group = self.get_mcp_access_group(access_group_id)
            except requests.HTTPError as exc:
                # A stale group reference must not prevent listing all other MCPs.
                if exc.response is not None and exc.response.status_code == 404:
                    continue
                raise
            allowed_server_ids.update(access_group.get("access_mcp_server_ids") or [])

        accessible_servers = []
        for server in servers:
            mcp_info = server.get("mcp_info") or {}
            owner = mcp_info.get("owner") if isinstance(mcp_info, dict) else None
            server_groups = set(server.get("mcp_access_groups") or [])

            # Visibility case 1 — owned MCP: the user registered this server through
            # PAPI, so mcp_info["owner"] contains their Keycloak subject ID.
            is_owned = owner == user_id

            # Visibility case 2 — public MCP: LiteLLM explicitly makes the server
            # available to every key with allow_all_keys=true.
            is_public = server.get("allow_all_keys") is True

            # Visibility case 3 — direct permission: at least one user key or team
            # explicitly contains this server_id in object_permission.mcp_servers.
            has_direct_access = server.get("server_id") in allowed_server_ids

            # Visibility case 4 — group permission: the user belongs, through a key
            # or team, to a group also assigned to this MCP. For example, both sides
            # contain "research", so their set intersection is not empty.
            has_group_access = bool(server_groups & allowed_legacy_mcp_groups)

            # A server is listed when at least one independent visibility case holds.
            if is_owned or is_public or has_direct_access or has_group_access:
                accessible_servers.append(server)

        return accessible_servers

    def delete_mcp_server(self, server_id: str) -> None:
        response = self.session.delete(
            f"{self.base_url}/v1/mcp/server/{server_id}",
            timeout=self.timeout,
        )
        response.raise_for_status()
