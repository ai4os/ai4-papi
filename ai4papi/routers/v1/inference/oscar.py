"""
Manage OSCAR clusters to create and execute services.
"""

from copy import deepcopy
from datetime import datetime
from functools import wraps
import json
from typing import List
import uuid
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
    image: str
    cpu: NonNegativeInt = 2
    memory: NonNegativeInt = 3000
    allowed_users: List[str] = []  # no additional users by default
    title: str = ""

    # Not configurable
    _name: str = ""  # filled by PAPI with UUID

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Demo image classification service",
                    "image": "deephdc/deep-oc-image-classification-tf",
                    "cpu": 2,
                    "memory": 3000,
                    "allowed_users": [],
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
        if r.ok:
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
        "cluster_id": MAIN_CONF["oscar"]["clusters"][vo]["cluster_id"],
        "endpoint": MAIN_CONF["oscar"]["clusters"][vo]["endpoint"],
        "oidc_token": token,
        "ssl": "true",
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
            "CLUSTER_ID": MAIN_CONF["oscar"]["clusters"][vo]["cluster_id"],
            "NAME": svc_conf._name,
            "IMAGE": svc_conf.image,
            "CPU": svc_conf.cpu,
            "MEMORY": svc_conf.memory,
            "ALLOWED_USERS": svc_conf.allowed_users,
            "VO": vo,
            "ENV_VARS": {
                "Variables": {
                    "PAPI_TITLE": svc_conf.title,
                    "PAPI_CREATED": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            },
        }
    )
    service = yaml.safe_load(service)

    return service


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
    auth.check_vo_membership(vo, auth_info["vos"])

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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Get services list
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.list_services()

    # Retrieve cluster config for MinIO info
    client_conf = client.get_cluster_config().json()

    # Filter services
    services = []
    for s in json.loads(r.text):
        # Filter out public services, if requested
        if not (s.get("allowed_users", None) or public):
            continue

        # Retrieve only services launched by PAPI
        if not s.get("name", "").startswith("ai4papi-"):
            continue

        # Keep only services that belong to vo
        if vo not in s.get("vo", []):
            continue

        # Add service endpoint for sync calls
        cluster_endpoint = MAIN_CONF["oscar"]["clusters"][vo]["endpoint"]
        s["endpoint"] = f"{cluster_endpoint}/run/{s['name']}"

        # Info for async calls
        # Replace MinIO info with the one retrieved from client (which is the correct one)
        s["storage_providers"]["minio"]["default"] = client_conf["minio_provider"]

        services.append(s)

    # Sort services by creation time, recent to old
    dates = [s["environment"]["Variables"]["PAPI_CREATED"] for s in services]
    idxs = sorted(range(len(dates)), key=dates.__getitem__)  # argsort
    sorted_services = [services[i] for i in idxs[::-1]]

    return sorted_services


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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Get service
    client = get_client_from_auth(authorization.credentials, vo)
    r = client.get_service(service_name)
    service = json.loads(r.text)

    # Add service endpoint
    cluster_endpoint = MAIN_CONF["oscar"]["clusters"][vo]["endpoint"]
    service["endpoint"] = f"{cluster_endpoint}/run/{service_name}"

    return service


@router.post("/services")
def create_service(
    vo: str,
    svc_conf: Service,
    authorization=Depends(security),
):
    """
    Creates a new inference service for an AI pre-trained model on a specific cluster.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Assign random UUID to service to avoid clashes
    # We clip it because OSCAR only seems to support names smaller than 39 characters
    svc_conf._name = f"ai4papi-{uuid.uuid1()}"[:39]

    # Create service definition
    service_definition = make_service_definition(svc_conf, vo)
    service_definition["allowed_users"] += [auth_info["id"]]  # add service owner

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    _ = client.create_service(service_definition)

    return svc_conf._name


@router.put("/services/{service_name}")
def update_service(
    vo: str,
    service_name: str,
    svc_conf: Service,
    authorization=Depends(security),
):
    """
    Updates service if it exists.
    The method needs all service parameters to be on the request.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Create service definition
    svc_conf._name = service_name
    service_definition = make_service_definition(svc_conf, vo)
    service_definition["allowed_users"] += [auth_info["id"]]  # add service owner

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    _ = client.update_service(svc_conf._name, service_definition)

    return service_name


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
    auth.check_vo_membership(vo, auth_info["vos"])

    # Delete service
    client = get_client_from_auth(authorization.credentials, vo)
    _ = client.remove_service(service_name)

    return service_name
