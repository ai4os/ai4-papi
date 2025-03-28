"""
Manage user secrets with Vault
"""

import hvac
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth


router = APIRouter(
    prefix="/secrets",
    tags=["Secrets management"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

# For now, we use for everyone the official EGI Vault server.
# We can reconsider this is we start using the IAM in auth.
VAULT_ADDR = "https://vault.services.fedcloud.eu:8200"
VAULT_AUTH_PATH = "jwt"
VAULT_ROLE = ""
VAULT_MOUNT_POINT = "/secrets/"


def vault_client(jwt, issuer):
    """
    Common init steps of Vault client
    """
    # Check we are using EGI Check-In prod
    if issuer != "https://aai.egi.eu/auth/realms/egi":
        raise HTTPException(
            status_code=400,
            detail="Secrets are only compatible with EGI Check-In Production OIDC "
            "provider.",
        )

    # Init the Vault client
    client = hvac.Client(
        url=VAULT_ADDR,
    )
    client.auth.jwt.jwt_login(
        role=VAULT_ROLE,
        jwt=jwt,
        path=VAULT_AUTH_PATH,
    )

    return client


def create_vault_token(
    jwt,
    issuer,
    ttl="1h",
):
    """
    Create a Vault token from a JWT.

    Parameters:
    * jwt: JSON web token
    * issuer: JWT issuer
    * ttl: duration of the token
    """
    client = vault_client(jwt, issuer)

    # When creating the client (`jwt_login`) we are already creating a login token with
    # default TTL (1h). So any newly created child token (independently of their TTL)
    # will be revoked after the login token expires (1h).
    # So instead of creating a child token, we have to *extend* login token.
    client.auth.token.renew_self(increment=ttl)

    # TODO: for extra security we should only allow reading/listing from a given subpath.
    # - Restrict to read/list can be done with user roles
    # - Restricting subpaths might not be done because policies are static (and
    #   deployment paths are dynamic). In addition only admins can create policies)

    return client.token


def recursive_path_builder(client, kv_list):
    """
    Reference: https://github.com/drewmullen/vault-kv-migrate
    """
    change = 0

    # if any list items end in '/' return 1
    for li in kv_list[:]:
        if li[-1] == "/":
            r = client.secrets.kv.v1.list_secrets(
                path=li, mount_point=VAULT_MOUNT_POINT
            )
            append_list = r["data"]["keys"]
            for new_item in append_list:
                kv_list.append(li + new_item)
            # remove list item ending in '/'
            kv_list.remove(li)
            change = 1

    # new list items added, rerun search
    if change == 1:
        recursive_path_builder(client, kv_list)

    return kv_list


@router.get("")
def get_secrets(
    vo: str,
    subpath: str = "",
    authorization=Depends(security),
):
    """
    Returns a list of secrets belonging to a user.

    Parameters:
    * **vo**: Virtual Organization where you belong.
    * **subpath**: retrieve secrets only from a given subpath.
      If not specified, it will retrieve all secrets from the user. \n
      Examples:
         - `/deployments/<deploymentUUID>/federated/`
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Init the Vault client
    client = vault_client(
        jwt=authorization.credentials,
        issuer=auth_info["issuer"],
    )

    # Check subpath syntax
    if not subpath.startswith("/"):
        subpath = "/" + subpath
    if not subpath.endswith("/"):
        subpath += "/"

    # Retrieve initial level-0 secrets
    user_path = f"users/{auth_info['id']}/{vo}"
    try:
        r = client.secrets.kv.v1.list_secrets(
            path=user_path + subpath, mount_point=VAULT_MOUNT_POINT
        )
        seed_list = r["data"]["keys"]
    except hvac.exceptions.InvalidPath:
        # InvalidPath is raised when there are no secrets available
        return {}

    # Now iterate recursively to retrieve all secrets from child paths
    for i, li in enumerate(seed_list):
        seed_list[i] = user_path + subpath + li
    final_list = recursive_path_builder(client, seed_list)

    # Extract secrets data
    out = {}
    for secret_path in final_list:
        r1 = client.secrets.kv.v1.read_secret(
            path=secret_path,
            mount_point=VAULT_MOUNT_POINT,
        )

        # Remove user-path prefix and save
        secret_path = secret_path.replace(user_path, "")
        out[secret_path] = r1["data"]

    return out


@router.post("")
def create_secret(
    vo: str,
    secret_path: str,
    secret_data: dict,
    authorization=Depends(security),
):
    """
    Creates a new secret or updates an existing one.

    Parameters:
    * **vo**: Virtual Organization where you belong.
    * **secret_path**: path of the secret.
      Not sensitive to leading/trailing slashes. \n
      Examples:
         - `/deployments/<deploymentUUID>/federated/<secret-name>`
    * **secret_data**: data to be saved at the path. \n
      Examples:
         - `{'token': 515c5d4f5d45fd15df}`
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Init the Vault client
    client = vault_client(
        jwt=authorization.credentials,
        issuer=auth_info["issuer"],
    )

    # Create secret
    client.secrets.kv.v1.create_or_update_secret(
        path=f"users/{auth_info['id']}/{vo}/{secret_path}",
        mount_point="/secrets/",
        secret=secret_data,
    )

    return {"status": "success"}


@router.delete("")
def delete_secret(
    vo: str,
    secret_path: str,
    authorization=Depends(security),
):
    """
    Delete a secret.

    Parameters:
    * **vo**: Virtual Organization where you belong.
    * **secret_path**: path of the secret.
      Not sensitive to leading/trailing slashes. \n
      Examples:
         - `deployments/<deploymentUUID>/fl-token`
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Init the Vault client
    client = vault_client(
        jwt=authorization.credentials,
        issuer=auth_info["issuer"],
    )

    # Delete secret
    client.secrets.kv.v1.delete_secret(
        path=f"users/{auth_info['id']}/{vo}/{secret_path}",
        mount_point=VAULT_MOUNT_POINT,
    )

    return {"status": "success"}
