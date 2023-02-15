"""
Get information from the API (no authentication needed)
"""

from fastapi import APIRouter

from ai4eosc.conf import USER_CONF


router = APIRouter(
    prefix="/info",
    tags=["info"],
    responses={404: {"description": "Not found"}},
)


@router.get("/conf")
def get_default_deployment_conf(
):
    """
    Returns default configuration for creating a deployment.

    Returns a dict.
    """
    return USER_CONF
