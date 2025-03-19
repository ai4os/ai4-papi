"""
Authentication for private methods of the API (mainly managing deployments)

Implementation notes:
====================
Authentication is implemented using `get_user_infos_from_access_token` instead
of `get_user_infos_from_request` (as done in the FastAPI example in the flaat docs).
There are two advantages of this:
* the main one is that it would enable us enable to take advantage of Swagger's builtin
  Authentication and Authorization [1] in the Swagger interface (generated by FastAPI).
  This is not possible using the `Request` object, as data from `Request` cannot be validated and
  documented by OpenAPI [2].

  [1] https://swagger.io/docs/specification/authentication/
  [2] https://fastapi.tiangolo.com/advanced/using-request-directly/?h=request#details-about-the-request-object

* the decorator `flaat.is_authenticated()` around each function is no longer needed,
  as authentication is checked automatically by `authorization=Depends(security)` without needing extra code.

The curl calls still remain the same, but now in the http://localhost/docs you will see an authorize
 button where you can copy paste your token. So you will be able to access authenticated methods from the interface.
"""

from fastapi import HTTPException
from flaat.fastapi import Flaat

from ai4papi.conf import MAIN_CONF


# Initialize flaat
flaat = Flaat()
flaat.set_trusted_OP_list(MAIN_CONF["auth"]["OP"])


def get_user_info(token):
    try:
        user_infos = flaat.get_user_infos_from_access_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
        )

    # Check output
    if user_infos is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
        )

    # Create a group dictionary where keys are the access levels and values are the
    # projects that enabled the user into that access level.
    # eg. {"platform-access": ["vo.ai4eosc.eu", "vo.imagine-ai.eu"]}
    groups = {}
    for i in user_infos.get("realm_access", {}).get("roles", []):
        i = i.split(":")
        access = i[0]  # eg. "platform-access"
        project = i[1] if len(i) > 1 else None  # eg. "vo.ai4eosc.eu"
        v = groups.get(access, [])
        if project:
            v.append(project)
        groups[access] = v

    # Generate user info dict
    for k in ["sub", "iss", "name", "email"]:
        if user_infos.get(k) is None:
            raise HTTPException(
                status_code=401,
                detail=f"You token should have scopes for {k}.",
            )

    # Check audiences (needed for Vault)
    if "account" not in user_infos.get("aud"):
        raise HTTPException(
            status_code=401,
            detail="You token should have 'account' in audiences.",
        )

    out = {
        "id": user_infos.get("sub"),  # subject, user-ID
        "issuer": user_infos.get("iss"),  # URL of the access token issuer
        "name": user_infos.get("name"),
        "email": user_infos.get("email"),
        "groups": groups,
    }

    return out


def check_authorization(
    auth_info: dict,
    requested_vo: str = None,
    access_level: str = "platform-access",
):
    """
    Check that the user has permissions to use the resource (usually "platform-access")
    and check he indeed belongs to the requested VO if one is specified.
    """
    if access_level not in auth_info["groups"].keys():
        raise HTTPException(
            status_code=401,
            detail=f"Your user has not the required access level to use this resource: {access_level}.",
        )

    user_vos = auth_info["groups"][access_level]
    if requested_vo and (requested_vo not in user_vos):
        raise HTTPException(
            status_code=401,
            detail=f"The requested Virtual Organization ({requested_vo}) does not match with any of your available VOs: {user_vos}.",
        )
