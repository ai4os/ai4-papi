# MCP server management

PAPI can register and manage
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers in
LiteLLM. A user provides the name of a server published in the supported Registry,
and PAPI decides whether it can use a provider-hosted remote endpoint or must
deploy a package in Nomad.

The integration provides three authenticated operations:

| Method | Endpoint | Operation |
|---|---|---|
| `POST` | `/v1/llm/mcp` | Register a remote MCP or create a Nomad deployment |
| `GET` | `/v1/llm/mcp` | List every MCP accessible to the user |
| `DELETE` | `/v1/llm/mcp/{server_id}` | Delete an MCP owned by the user |

Every endpoint receives a Keycloak OIDC token through
`Authorization: Bearer <token>`. PAPI uses the token's `sub` claim as the stable
owner identifier in LiteLLM and Nomad.

## Current features and limitations

The current implementation supports:

- The official MCP Registry, configurable through `MCP_REGISTRY_URL`.
- Remote `streamable-http` endpoints that do not require headers or variable
  substitution.
- Deployable npm packages with native `streamable-http` transport.
- npm `stdio` packages exposed as Streamable HTTP through Supergateway.
- CPU, RAM and disk configuration for Nomad deployments.
- Automatic registration and deregistration of Nomad jobs in LiteLLM.
- Private servers using one stable access group per user.
- Listing owned and public MCPs, as well as MCPs granted through keys, teams and
  groups.

The current implementation does not yet support:

- SSE transport, either remote or deployable.
- Package registries other than npm.
- Custom npm registries.
- Required secrets or arguments supplied by the user when creating an MCP. The
  `conf` field configures Nomad resources, not package variables.
- Remote endpoints that require headers, credentials or provider-specific
  parameter substitution.

## Code organization

MCP-specific functionality is located in the `ai4papi.mcp` subpackage:

```text
ai4papi/mcp/
├── __init__.py
├── registry.py
├── packages.py
├── transports.py
└── nomad.py
```

| Module | Responsibility |
|---|---|
| `registry.py` | Query and normalize metadata from the official Registry |
| `packages.py` | Select npm packages and validate arguments and environment variables |
| `transports.py` | Handle Streamable HTTP, `stdio` adaptation and Basic Auth |
| `nomad.py` | Build, inspect and delete MCP jobs in Nomad |

The HTTP router is implemented in `ai4papi/routers/v1/llm/mcp.py`. Shared
operations against the LiteLLM management API are encapsulated in
`ai4papi/litellm_client.py`.

The resources injected into jobs are located under `etc/mcp/`:

```text
etc/mcp/
├── nomad.hcl       # Nomad job template
├── user.yaml       # Default resources and allowed ranges
├── register.py     # Poststart registration task
└── deregister.py   # Poststop deregistration task
```

## Supported Registry

PAPI currently queries the
[official MCP Registry](https://registry.modelcontextprotocol.io/). Although its
URL is configurable, the client expects the schema and metadata defined by the
official Registry. Changing the URL does not provide generic support for other
registries.

The client searches `/v0.1/servers` and then requires an exact match for the
requested name. It only accepts the entry marked by the Registry as active and as
the latest version:

```text
server.name == requested name
metadata.status == "active"
metadata.isLatest == true
```

The response is normalized into one of these types:

- `remote`: contains one or more endpoints already deployed by the provider.
- `deployable`: contains one or more packages that PAPI may execute.
- `hybrid`: contains both remote endpoints and deployable packages.

The type describes the options available in the Registry entry; it does not force
a particular deployment method. If a hybrid entry provides a usable remote
Streamable HTTP endpoint, PAPI registers it directly and avoids consuming Nomad
resources. If its remote endpoints are unsupported but it contains a valid npm
package, PAPI attempts the self-deployed flow.

### Selecting a remote endpoint

A remote endpoint is accepted when:

- its type is `streamable-http`;
- its URL is a string with no unresolved `{variable}` placeholders;
- it declares no required headers that the user would have to provide.

PAPI registers this transport as `http` in LiteLLM. A server that only provides
SSE is currently rejected.

### Selecting a deployable package

PAPI only selects packages whose `registryType` is `npm`. If an entry contains
multiple variants, it uses the following preference order:

1. npm with native `streamable-http` transport;
2. npm with `stdio` transport.

The native variant avoids installing an adapter and has fewer processes and
failure points. The package version is pinned. If the package does not include a
version, PAPI inherits the version from the enclosing server entry.

> [!WARNING]
> The endpoint does not accept npm names such as
> `@modelcontextprotocol/server-everything` directly. The `name` field must always
> contain the full name of a server in the Registry. Its npm identifier is obtained
> from the Registry metadata after the entry has been validated.

## Package, argument and environment validation

All validation happens before the job is submitted to Nomad. This prevents the
creation of deployments that cannot start and prevents Registry-provided text from
being interpreted as shell syntax.

### Package identity

For an npm package, PAPI verifies:

- that `identifier` is a valid scoped or unscoped npm name;
- that the version is pinned and has a valid format;
- that `registryBaseUrl`, when present, is `https://registry.npmjs.org`;
- that `runtimeHint`, when present, is `npx`.

The resulting package reference is always pinned:

```text
@organization/package@1.2.3
```

If `runtimeArguments` already contains the package with another tag such as
`latest`, PAPI replaces it with the selected version and prevents duplicate
package references.

### Arguments

Both `runtimeArguments` and `packageArguments` are processed. Only the following
types are accepted:

- `positional`: adds the value as a separate `argv` element;
- `named`: requires the name to begin with `-` and produces `--name=value`.

The value is taken first from `value` and then, when absent, from `default`. If the
Registry marks an argument as `isRequired` but provides neither value, the
deployment is rejected. PAPI does not yet allow the caller to supply that value in
the `POST` body.

Variables declared inside an argument are substituted using their own `value` or
`default`. Any remaining `{VARIABLE}` placeholder causes validation to fail before
Nomad is contacted.


### Environment variables

Each entry in `environmentVariables` must have a name that matches:

```text
[A-Za-z_][A-Za-z0-9_]*
```

The same `value`, `default`, `isRequired` and placeholder substitution rules used
for arguments also apply to environment variables. Resolved variables are added to
the main task environment. PAPI also sets `HOST=0.0.0.0` and the selected `PORT`
to integrate the process with Nomad networking.

### Local Streamable HTTP endpoint

A package with native Streamable HTTP transport must declare a local URL.
Placeholders are resolved from environment variables, argument names and
`valueHint`. PAPI then verifies that the URL:

- uses `http://`;
- uses `localhost`, `127.0.0.1`, `0.0.0.0` or `::1` as its host;
- contains an explicit unprivileged port greater than or equal to 1024;
- contains no username, password, query string or fragment;
- requires no transport headers.

The resulting port and path configure the Nomad service, Traefik, the
initialization check and the public URL registered in LiteLLM.

### Nomad resources

Default values are defined in `etc/mcp/user.yaml`:

| Resource | Default | Allowed range |
|---|---:|---:|
| CPU | 1 | 1–10 |
| RAM | 1024 MB | 512–40000 MB |
| Disk | 2048 MB | 1000–50000 MB |



## Supergateway and deployable transports

LiteLLM must be able to reach a deployed MCP through a URL. A `stdio` package does
not open a network port: it exchanges JSON-RPC messages through the process's
standard input and output. To make it reachable through Traefik and LiteLLM, PAPI
places [Supergateway](https://github.com/supercorp-ai/supergateway) in front of the
process:

```text
LiteLLM → HTTPS/Traefik → Streamable HTTP :8000/mcp
                         → Supergateway → stdio MCP process
```

Supergateway converts `stdio` into Streamable HTTP. A pinned Supergateway version,
configured through `MCP_NOMAD_SUPERGATEWAY_PACKAGE`, is installed together with
the pinned MCP package in a single dependency tree under
`/tmp/papi-mcp-runtime`. The MCP process is then run with `npx --offline`. This
avoids two temporary `npx` installations racing with one another or leaving
incomplete dependencies behind.

Packages with native Streamable HTTP are run directly with `npx`; they do not pass
through Supergateway. In both cases LiteLLM sees a Streamable HTTP interface, while
the metadata retains both the package's original transport and the exposed
transport.

## Deploying a remote MCP

The remote flow is synchronous:

```text
User → PAPI → Registry → LiteLLM → user's access group
```

1. PAPI authenticates the user and obtains their Keycloak identifier.
2. It queries and validates the latest active Registry entry.
3. It selects a remote Streamable HTTP endpoint with no pending configuration.
4. It creates the server in LiteLLM with `allow_all_keys: false`.
5. It retrieves all existing keys owned by the user.
6. It creates or updates the user's private access group with the server and keys.
7. It stores the owner, Registry and access-group relationship in `mcp_info`.
8. It returns the complete registration while redacting credentials and static
   headers.

If a step fails after the server has been created, PAPI attempts to undo the grant
and registration in reverse order so that a retry starts from a clean state.

A remote MCP creates no Nomad job and stores `nomad_job_id: null`.

## Deploying a self-deployed MCP

The self-deployed flow is asynchronous:

```text
User → PAPI → Registry → validation → Nomad
                                          │
                         poststart ────────┘
                             ↓
                initialize MCP → LiteLLM → access group
```

1. PAPI authenticates the user and verifies membership in the requested VO.
2. It queries the Registry and selects a compatible npm package.
3. It resolves and validates the package, version, arguments, environment and
   transport.
4. It merges submitted resources with defaults and validates their ranges.
5. It generates a `job_id` with the `mcp-` prefix and a random Basic Auth
   credential.
6. It builds a job from `etc/mcp/nomad.hcl` and submits it to Nomad.
7. It immediately returns `status: submitted`, without waiting for scheduling,
   Traefik, MCP startup or LiteLLM registration.

The job contains three tasks:

- `main`: runs the Streamable HTTP MCP directly or runs Supergateway with the
  `stdio` MCP as its child process.
- `register_mcp_in_litellm`: a `poststart` lifecycle task that waits for the MCP
  and registers it.
- `deregister_mcp_from_litellm`: a `poststop` lifecycle task that revokes access
  and deletes the registration.

The registration and deregistration tasks use `sidecar = false`: they are finite
lifecycle tasks, not services that remain running for the lifetime of the
allocation.

### Automatic registration (`poststart`)

The `poststart` task does not treat an open TCP port as sufficient evidence of
readiness. It sends an MCP `initialize` request to the local endpoint and requires
a successful JSON-RPC result. It can parse either a JSON response or the first
valid event in a `text/event-stream` response. This detects cases such as a running
Supergateway whose MCP child process terminated because a required environment
variable was missing.

After the MCP responds, the task:

1. searches for a LiteLLM server with a matching `nomad_job_id`, making retries
   idempotent;
2. creates it with the public Traefik URL and `allow_all_keys: false` when it does
   not exist;
3. stores the Basic header in `static_headers.Authorization` so LiteLLM can pass
   through Traefik authentication;
4. creates or updates the owner's stable access group;
5. updates `mcp_info` with the group's ID and name.

The password hash is stored in the Traefik Basic Auth middleware. LiteLLM receives
the corresponding `Authorization: Basic ...` header as a static header. PAPI
redacts its value from API responses.

The complete Basic header and central administrative key are currently injected
into the lifecycle task environment. Permission to inspect Nomad jobs and
allocations must therefore be treated as privileged. A natural future improvement
would be to deliver these credentials through Vault or Nomad secret templates.

### Automatic deregistration (`poststop`)

The `poststop` task locates the server by `nomad_job_id`, removes its ID from the
user's access group and deletes the server from LiteLLM. It retains the group even
when it becomes empty so it can be reused for future MCPs.

The `DELETE` endpoint also performs LiteLLM cleanup synchronously after stopping
Nomad. The `poststop` task acts as a fallback when the job is deleted directly in
Nomad. The script treats an already absent registration as success and ignores a
`404` when deleting it, reducing conflicts if both cleanup paths overlap.

### Nomad and LiteLLM metadata

Nomad retains enough metadata to inspect the job without querying the Registry
again: owner, Registry name and version, package identity and transport, exposed
path and resources.

LiteLLM stores an `mcp_info` structure similar to:

```json
{
  "owner": "<keycloak-sub>",
  "source": "official-mcp-registry",
  "registry_name": "com.example/server",
  "registry_version": "1.2.3",
  "deployment_type": "self_deployed",
  "nomad_job_id": "mcp-<uuid>",
  "nomad_namespace": "ai4eosc",
  "package_registry_type": "npm",
  "package_identifier": "@example/server",
  "package_version": "1.2.3",
  "package_transport": "stdio",
  "exposed_transport": "streamable-http",
  "access_group_id": "<litellm-access-group-id>",
  "access_group_name": "papi_user_mcp_<digest>"
}
```

`owner` is authorization information used by PAPI to determine who may delete the
MCP. It does not grant a LiteLLM key permission to execute the MCP by itself; that
permission comes from the access group.

## Access policy: one access group per user

Every server created by PAPI is private:

```json
{"allow_all_keys": false}
```

PAPI creates at most one stable MCP access group for each Keycloak user. Its name
does not contain the original identifier; it uses a deterministic digest:

```text
papi_user_mcp_<sha256(keycloak-sub)[:12]>
```

The relationship is:

```text
Keycloak user
└── private MCP access group
    ├── user's keys
    └── MCP 1, MCP 2, ...
```

PAPI does not create one access group per MCP. Adding or removing a server only
changes the stable group's `access_mcp_server_ids` list. Each key therefore needs
only one group reference regardless of how many MCPs the user owns.

### Existing and future keys

When an MCP is created, PAPI or the `poststart` task retrieves the owner's current
keys and updates the group with:

```json
{
  "access_mcp_server_ids": ["<server-id-1>", "<server-id-2>"],
  "assigned_key_ids": ["<key-id-1>", "<key-id-2>"],
  "assigned_team_ids": []
}
```

When the user later creates a key through PAPI, the keys router looks up the
deterministic group name and sends its ID in `access_group_ids` during
`/key/generate`. Consequently:

- a new MCP is added to the group and becomes available to existing keys;
- a new key references the existing group and receives all current MCPs;
- MCPs added later do not require that key to be updated individually.

If the user does not yet have any keys, the group may exist with
`assigned_key_ids: []`. The MCP still appears as owned in PAPI's listing, but the
user must create a key associated with the group before invoking it through
LiteLLM.

### Why the group is not assigned to the team

Keys continue to belong to the team associated with the user's authorization
level, for example `ap-d`. That team is shared and has other responsibilities in
LiteLLM.

PAPI does not add the private MCP to the team and always keeps
`assigned_team_ids: []`. Assigning the group to `ap-d` would allow every user in
that team to inherit the MCP. PAPI also does not create individual teams for each
user.

Using `object_permission.mcp_servers` as the primary mechanism for user-owned MCPs
was rejected. LiteLLM evaluates direct key permissions within the limits of the
key's team: an `ap-d` key cannot request MCP servers that the team does not allow.
This caused errors when adding a private MCP to a key without also broadening the
shared team's permissions. A unified access group assigned directly to the key
keeps the private permission separate from the team's general permissions.

In summary:

```text
team_id = ap-d                     shared general authorization level
access_group_ids = [user group]    private MCP permission
```

### External permissions and listing

PAPI only manages groups whose names follow the
`papi_user_mcp_<digest>` pattern. However, listing also honors other access methods
that may have been configured directly in LiteLLM:

- MCPs owned by the user according to `mcp_info.owner`;
- public MCPs with `allow_all_keys: true`;
- servers granted directly to a key or team through
  `object_permission.mcp_servers`;
- named MCP groups granted through `object_permission.mcp_access_groups`;
- unified access groups referenced by `access_group_ids` on a key or team.

This allows `GET /v1/llm/mcp` to also return MCPs shared by an administrator or
inherited from a team. Visibility does not imply ownership: only the user whose
identifier is stored in `mcp_info.owner` may delete the server through PAPI.

## Endpoints

### Create or deploy an MCP

```http
POST /v1/llm/mcp
Authorization: Bearer <OIDC token>
Content-Type: application/json
```

Request body:

```json
{
  "name": "exact.name/in-the-registry",
  "vo": "vo.ai4eosc.eu",
  "conf": {
    "hardware": {
      "cpu_num": 2,
      "ram": 2048,
      "disk": 4096
    }
  }
}
```

| Field | Required | Purpose |
|---|---|---|
| `name` | Yes | Exact server name in the official Registry |
| `vo` | Only relevant to self-deployed MCPs | Deployment VO; defaults to `MCP_NOMAD_DEFAULT_VO` when omitted |
| `conf` | No | Partial resource configuration; only used for self-deployed MCPs |

For a remote MCP, `vo` and `conf` are ignored. A successful remote response uses
HTTP status `201` and has the following summarized form:

```json
{
  "name": "com.example/weather",
  "version": "1.0.0",
  "deployment_type": "remote",
  "endpoint": {
    "transport": "http",
    "url": "https://provider.example/mcp"
  },
  "registration": {
    "server_id": "<litellm-server-id>",
    "allow_all_keys": false,
    "mcp_info": {
      "owner": "<keycloak-sub>",
      "deployment_type": "remote",
      "access_group_id": "<access-group-id>"
    }
  },
  "deployment": null
}
```

A successful self-deployed response also uses HTTP status `201`, but it only
confirms that Nomad accepted the job:

```json
{
  "name": "com.example/filesystem",
  "version": "1.0.0",
  "deployment_type": "self_deployed",
  "endpoint": null,
  "registration": null,
  "deployment": {
    "job_id": "mcp-<uuid>",
    "namespace": "ai4eosc",
    "status": "submitted",
    "endpoint": null
  }
}
```

The registration appears in the listing after the `poststart` task has completed.
During that interval, LiteLLM is not yet aware of the job.

Example:

```bash
curl -sS -X POST "http://127.0.0.1:8080/v1/llm/mcp" \
  -H "Authorization: Bearer ${PAPI_TEST_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "exact.name/in-the-registry",
    "vo": "vo.ai4eosc.eu",
    "conf": {"hardware": {"cpu_num": 1, "ram": 1024, "disk": 2048}}
  }' | jq
```

Common errors:

- `400`: resources outside the allowed range;
- `403`: the user is not authorized for the requested VO;
- `404`: the name has no latest active version in the Registry;
- `422`: unsupported Registry transport, package or configuration;
- `502`: LiteLLM, Nomad or Registry connection error;
- `504`: Registry query timeout.

### List accessible MCPs

```http
GET /v1/llm/mcp
Authorization: Bearer <OIDC token>
```

Example:

```bash
curl -sS "http://127.0.0.1:8080/v1/llm/mcp" \
  -H "Authorization: Bearer ${PAPI_TEST_TOKEN}" | jq
```

The response is a list. Each item contains:

- `name` and `version`, taken from `mcp_info` when available;
- `deployment_type`, normally `remote` or `self_deployed`;
- `endpoint`, containing the transport and URL registered in LiteLLM;
- `registration`, containing the LiteLLM record with secrets redacted;
- `deployment`, containing the Nomad state for self-deployed MCPs, or `null` for
  remote MCPs.

LiteLLM is the catalog and initial source of visibility. PAPI does not enumerate
all jobs in a namespace. It only queries Nomad for accessible MCPs whose `mcp_info`
contains `deployment_type: self_deployed`, `nomad_job_id` and `nomad_namespace`.

The enriched Nomad state contains:

```json
{
  "job_id": "mcp-<uuid>",
  "namespace": "ai4eosc",
  "status": "running",
  "endpoint": "https://mcp-<uuid>.<node>-<base-domain>/mcp",
  "allocation_id": "<nomad-allocation-id>",
  "healthy": true,
  "registration_status": "registered",
  "error_message": null,
  "submitted_at": "2026-09-09T12:00:00",
  "registry_name": "com.example/server",
  "registry_version": "1.2.3",
  "package": {
    "registry_type": "npm",
    "identifier": "@example/server",
    "version": "1.2.3",
    "transport": "stdio"
  },
  "resources": {
    "cpu_num": 1,
    "ram": 1024,
    "disk": 2048
  }
}
```

`healthy` is true only when Nomad marks the allocation as healthy, the main task
is running and the registration task completed successfully. If a registration
references a job that no longer exists, PAPI returns `status: missing` and
`healthy: false` for that deployment.

### Delete an MCP

```http
DELETE /v1/llm/mcp/{server_id}
Authorization: Bearer <OIDC token>
```

`server_id` is the LiteLLM registration ID, not the Nomad `job_id` or Registry
name.

Example:

```bash
curl -i -X DELETE \
  "http://127.0.0.1:8080/v1/llm/mcp/${MCP_SERVER_ID}" \
  -H "Authorization: Bearer ${PAPI_TEST_TOKEN}"
```

On success, the endpoint returns HTTP `204 No Content`.

For a remote MCP, PAPI removes the server from the owner's access group and
deletes it from LiteLLM. For a self-deployed MCP, PAPI:

1. verifies that `mcp_info.owner` matches the user;
2. verifies that the namespace belongs to an authorized VO;
3. purges the Nomad job;
4. removes the server from the stable access group;
5. deletes the LiteLLM registration.

Read or execution access to a shared MCP does not grant permission to delete it.
If the user is not the owner, PAPI returns `403` before performing any destructive
operation in Nomad or LiteLLM.

## Configuration

The main environment variables are:

| Variable | Description | Development default |
|---|---|---|
| `LITELLM_URL` | URL of the central LiteLLM instance | No functional value |
| `LITELLM_API_KEY` | Administrative key used internally by PAPI and lifecycle tasks | No value |
| `LITELLM_TIMEOUT` | Management API request timeout | `90` seconds |
| `MCP_REGISTRY_URL` | MCP Registry queried by PAPI | Must be configured; normally the official Registry |
| `MCP_REGISTRY_TIMEOUT` | Registry request timeout | `90` seconds |
| `MCP_NOMAD_DEFAULT_VO` | VO used when `POST` omits `vo` | `vo.ai4eosc.eu` |
| `MCP_NOMAD_STARTUP_TIMEOUT` | Maximum time `poststart` waits for `initialize` | `180` seconds |
| `MCP_NOMAD_POLL_INTERVAL` | Delay between initialization attempts | `2` seconds |
| `MCP_NOMAD_NODE_IMAGE` | Image used to run the npm package | `node:22-alpine` |
| `MCP_NOMAD_SUPERGATEWAY_PACKAGE` | Pinned version of the stdio adapter | `supergateway@3.4.3` |

The standard Nomad access variables (`NOMAD_ADDR`, `NOMAD_CACERT`,
`NOMAD_CLIENT_CERT` and `NOMAD_CLIENT_KEY`) are also required. VO-to-namespace and
public-domain mappings are loaded from `etc/main.yaml`.

`LITELLM_API_KEY` is an internal administrative credential and must never be
returned to the user. Values stored in `static_headers` and `credentials` are also
redacted from PAPI responses.
