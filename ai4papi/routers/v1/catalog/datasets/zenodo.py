"""
This route mimics Zenodo API and return results as is.

PAPI is acting as a proxy between the Dashboard and Zenodo because we want to
make *authenticated* calls with Zenodo. And we cannot have a Zenodo token in the
Dashboard because the calls are being run on the client side (ie. the client would see
the Zenodo token).
"""

import os
import re
import requests
from typing import Union


from cachetools import cached, TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth


router = APIRouter(
    prefix="/zenodo",
    tags=["Zenodo datasets"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

# If available, authenticate the call to Zenodo to increase rate limit.
# https://developers.zenodo.org/#rate-limiting
API_URL = 'https://zenodo.org'
session = requests.Session()
zenodo_token = os.environ.get('ZENODO_TOKEN', None)
if zenodo_token:
    session.headers = {
        'Authorization': f'Bearer {zenodo_token}',
    }


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def _zenodo_proxy(
    api_route: str,
    params: Union[dict, None] = None,
    ):
    """
    We use this hidden function to allow for caching responses.
    Otherwise error will be raised, because "authorization" param cannot be cached
    --> TypeError: unhashable type: 'types.SimpleNamespace'
    """
    # To avoid security issues, only allow a subset of Zenodo API (to avoid users
    # using *our* Zenodo token to update any record)
    allowed_routes = [
        '^communities/[a-zA-Z0-9-]+/records*$',
        '^records/[0-9]+/versions*$',
        ]
    allowed = False
    for i in allowed_routes:
        if re.match(i, api_route):
            allowed = True
            break
    if not allowed:
        raise HTTPException(
            status_code=400,
            detail="Zenodo API route not allowed."  \
                   f"Allowed routes: {allowed_routes}",
            )

    # Make the call
    r = session.get(
        f"{API_URL}/api/{api_route}",
        params=params,
        )

    if not r.ok:
        raise HTTPException(
            status_code=500,
            detail="Failed to query Zenodo.",
            )

    return r.json()


@router.get("/")
def zenodo_proxy(
    api_route: str,
    params: Union[dict, None] = None,
    authorization=Depends(security),
    ):
    """
    Zenodo proxy

    Parameters
    ----------
    * api_route:
      For example:
        - communities/imagine-project/records
        - records/11195949/versions
    * params:
      Any additional params the Zenodo call might need for that given route.
    """
    # To avoid DDoS in Zenodo, only allow access to EGI authenticated users.
    _ = auth.get_user_info(token=authorization.credentials)

    return _zenodo_proxy(api_route, params)
