"""
Manage OSCAR clusters to create and execute services.
"""
from copy import deepcopy
from functools import wraps
import json
from typing import List
import yaml

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
from oscar_python.client import Client
from pydantic import BaseModel, NonNegativeInt
import requests

from ai4papi import auth
from ai4papi.conf import MAIN_CONF, OSCAR_TMPL


router = APIRouter(
    prefix="/oscar",
    tags=["OSCAR inference"],
    responses={404: {"description": "Inference not found"}},
)

class Service(BaseModel):
    name: str
    image: str
    cpu: NonNegativeInt = 2
    memory: NonNegativeInt = 3000
    input_type: str
    allowed_users: List[str] = []  # no additional users by default

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "demo-service-papi",
                    "image": "deephdc/deep-oc-image-classification-tf",
                    "cpu": 2,
                    "memory": 3000,
                    "input_type": "str",
                    "allowed_users": ["string"]
                }
            ]
        }
    }

security = HTTPBearer()


def raise_for_status(func):
    """
    Raise HTML error if the response of OSCAR functions has status!=2**.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):

        # Catch first errors happening internally
        try:
            r = func(*args, **kwargs)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=e,
                )
        except requests.exceptions.HTTPError as e:
            raise HTTPException(
                status_code=500,
                detail=e,
                )

        # Catch errors when the function itself does not raise errors but the response
        # has a non-successful code
        if str(r.status_code).startswith('2'):
            return r
        else:
            raise HTTPException(
                status_code=r.status_code,
                detail=r.text,
                )

    return wrapper


def get_client_from_auth(token, vo):
    """
    Retrieve authenticated user info and init OSCAR client.
    """
    client_options = {
        'cluster_id': MAIN_CONF["oscar"]["clusters"][vo]['cluster_id'],
        'endpoint': MAIN_CONF["oscar"]["clusters"][vo]['endpoint'],
        'oidc_token': token,
        'ssl': 'true',
        }

    try:
        client = Client(client_options)
    except Exception:
        raise Exception("Error creating OSCAR client")

    # Decorate Client functions to propagate OSCAR status codes to PAPI
    client.get_cluster_info = raise_for_status(client.get_cluster_info)
    client.list_services = raise_for_status(client.list_services)
    client.get_service = raise_for_status(client.get_service)
    client.create_service = raise_for_status(client.create_service)
    client.update_service = raise_for_status(client.update_service)
    client.remove_service = raise_for_status(client.remove_service)
    # client.run_service = raise_for_status(client.run_service)  #TODO: reenable when ready?

    return client


def make_service_definition(svc_conf, vo):
    # Create service definition
    service = deepcopy(OSCAR_TMPL)  # init from template
    service = service.safe_substitute(
        {
            'NAME': svc_conf.name,
            'IMAGE': svc_conf.image,
            'CPU': svc_conf.cpu,
            'MEMORY': svc_conf.memory,
            'TYPE': svc_conf.input_type,
            'ALLOWED_USERS': svc_conf.allowed_users,
            'VO': vo,
        }
    )
    service = yaml.safe_load(service)

    # Create service url
    cluster_id = MAIN_CONF["oscar"]["clusters"][vo]["cluster_id"]
    endpoint = MAIN_CONF["oscar"]["clusters"][vo]["endpoint"]
    url = f"{endpoint}/services/{cluster_id}/{svc_conf.name}"

    return service, url


@router.get("/cluster")
def get_cluster_info(
    vo: str,
    authorization=Depends(security),
    ):
    """
    Gets information about the cluster.
    - Returns a JSON with the cluster information.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Get cluster info
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.get_cluster_info()

    return json.loads(r.text)


@router.get("/services")
def get_services_list(
    vo: str,
    public: bool = Query(default=False),
    authorization=Depends(security),
    ):
    """
    Retrieves a list of all the deployed services of the cluster.

    **Parameters**
    * **public**: whether to retrieve also public services, not specifically tied to
      your particular user.

    - Returns a JSON with the cluster information.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Get services list
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.list_services()

    # Filter services
    services = []
    for s in json.loads(r.text):
        # Filter out public services, if requested
        if not (s.get('allowed_users', None) or public):
            continue
        # Keep only services that belong to vo
        if vo not in s.get('vo', []):
            continue
        services.append(s)

    return services


@router.get("/services/{service_name}")
def get_service(
    vo: str,
    service_name: str,
    authorization=Depends(security),
    ):
    """
    Retrieves a specific service.
    - Returns a JSON with the cluster information.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Get service
    client = get_client_from_auth(authorization.credentials, vo)
    result = client.get_service(service_name)

    return json.loads(result.text)


@router.post("/services")
def create_service(
    vo: str,
    svc_conf: Service,
    authorization=Depends(security),
    ):
    """
    Creates a new inference service for an AI pre-trained model on a specific cluster
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Create service definition
    service_definition, service_url = make_service_definition(svc_conf, vo)
    service_definition['allowed_users'] += [auth_info['id']]  # add service owner

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.create_service(service_definition)

    return service_url


@router.put("/services/{service_name}")
def update_service(
    vo: str,
    svc_conf: Service,
    authorization=Depends(security),
    ):
    """
    Updates service if it exists.
    The method needs all service parameters to be on the request.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Create service definition
    service_definition, service_url = make_service_definition(svc_conf, vo)
    service_definition['allowed_users'] += [auth_info['id']]  # add service owner

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.update_service(svc_conf.name, service_definition)

    return service_url


@router.delete("/services/{service_name}")
def delete_service(
    vo: str,
    service_name: str,
    authorization=Depends(security),
    ):
    """
    Delete a specific service.
    Raises 500 if the service does not exists.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Delete service
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.remove_service(service_name)

    return service_name


# TODO: inference is temporarily disabled until we define how the Dashboard is going to
# use OSCAR services

# @router.post("/services/{service_name}")
# def inference(
#     vo: str,
#     service_name: str,
#     data: dict,
#     authorization=Depends(security),
#     ):
#     """
#     Make a synchronous execution (inference)
#     """
#     # Retrieve authenticated user info
#     auth_info = auth.get_user_info(authorization.credentials)
#     auth.check_vo_membership(vo, auth_info['vos'])

#     # Make inference
#     client = get_client_from_auth(authorization.credentials, vo)
#     r = client.run_service(service_name, input=data["input_data"])
#     try:
#         return (r.status_code, json.loads(r.text))
#     except Exception:
#         return (r.status_code, r.text)
