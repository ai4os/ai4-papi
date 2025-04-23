"""
Manage OSCAR clusters to create and execute services.
"""

from copy import deepcopy
from datetime import datetime
from functools import wraps
import json
from typing import Union
import uuid
import yaml

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer
from oscar_python.client import Client
import requests

from ai4papi import auth, utils
from ai4papi.routers.v1.catalog.modules import Modules
from ai4papi.routers.v1.catalog.common import retrieve_docker_tags
import ai4papi.conf as papiconf


router = APIRouter(
    prefix="/oscar",
    tags=["OSCAR inference"],
    responses={404: {"description": "Inference not found"}},
)

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
        "cluster_id": papiconf.MAIN_CONF["oscar"]["clusters"][vo]["cluster_id"],
        "endpoint": papiconf.MAIN_CONF["oscar"]["clusters"][vo]["endpoint"],
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

    return client


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


@router.get("/conf")
def get_service_conf(
    item_name: str,
    vo: str,
):
    """
    Returns the configuration needed to make an OSCAR service deployment.
    """
    # Check if module exists
    modules = Modules.get_items()
    if item_name not in modules.keys():
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available module.",
        )

    # Retrieve module configuration
    conf = deepcopy(papiconf.OSCAR["user"]["full"])

    # Retrieve module metadata
    metadata = Modules.get_metadata(item_name)

    # Parse docker registry
    registry = metadata["links"]["docker_image"]
    repo, image = registry.split("/")[-2:]
    if repo not in ["deephdc", "ai4oshub"]:
        repo = "ai4oshub"

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

    # Add available Docker tags
    tags = retrieve_docker_tags(image=image, repo=repo)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify default hardware value with user preferences, as long as they are within
    # the allowed limits
    meta_inference = metadata.get("resources", {}).get("inference", {})  # user request
    final = {}  # final deployment values
    mismatches = {}
    meta2conf = {
        "cpu": "cpu_num",
        "memory_MB": "ram",
    }
    for k, v in meta2conf.items():
        final[k] = meta_inference.get(k, conf["hardware"][v]["value"])
        final[k] = max(final[k], conf["hardware"][v]["range"][0])
        final[k] = min(final[k], conf["hardware"][v]["range"][1])
        conf["hardware"][v]["value"] = final[k]
        if (user_k := meta_inference.get(k)) and user_k > final[k]:
            mismatches[k] = f"Requested: {user_k}, Max allowed: {final[k]}"

    # Show warning if we couldn't accommodate user requirements
    if mismatches:
        warning = (
            "The developer of the module specified an minimum amount of resources "
            "that could not be met in OSCAR deployments. "
            "Therefore, you might experience some issues when using this module for "
            "inference. \n The following resources could not be met: <ul>"
        )
        for k, v in mismatches.items():
            warning += f"\n<li> <strong>{k}</strong>: {v} </li>"
        conf["hardware"]["warning"] = warning + "</ul>"

    return conf


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
        cluster_endpoint = papiconf.MAIN_CONF["oscar"]["clusters"][vo]["endpoint"]
        s["endpoint"] = f"{cluster_endpoint}/run/{s['name']}"

        # Info for async calls
        # Replace MinIO info with the one retrieved from client (which is the correct one)
        s["storage_providers"]["minio"]["default"] = client_conf["minio_provider"]

        services.append(s)

    # Sort services by creation time, recent to old
    dates = [s["environment"]["variables"]["PAPI_CREATED"] for s in services]
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
    cluster_endpoint = papiconf.MAIN_CONF["oscar"]["clusters"][vo]["endpoint"]
    service["endpoint"] = f"{cluster_endpoint}/run/{service_name}"

    return service


def make_service_definition(conf, vo):
    """
    Generate an OSCAR service definition. It is used both to create and to update a
    service.
    """

    # Create service definition
    service = deepcopy(papiconf.OSCAR["service"])  # init from template
    service = service.safe_substitute(
        {
            "CLUSTER_ID": papiconf.MAIN_CONF["oscar"]["clusters"][vo]["cluster_id"],
            "NAME": conf["name"],
            "IMAGE": conf["general"]["docker_image"],
            "CPU": conf["hardware"]["cpu_num"],
            "MEMORY": conf["hardware"]["ram"],
            "ALLOWED_USERS": conf["allowed_users"],
            "VO": vo,
            "ENV_VARS": {
                "Variables": {
                    "PAPI_TITLE": conf["general"]["title"],
                    "PAPI_DESCRIPTION": conf["general"]["desc"],
                    "PAPI_CREATED": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            },
        }
    )
    service = yaml.safe_load(service)

    return service


@router.post("/services")
def create_service(
    vo: str,
    conf: Union[dict, None] = None,
    authorization=Depends(security),
):
    """
    Creates a new inference service for an AI pre-trained model on a specific cluster.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Load service configuration
    user_conf = deepcopy(papiconf.MODULES["user"]["values"])

    # Update values conf in case we received a submitted conf
    if conf is not None:
        user_conf = utils.update_values_conf(
            submitted=conf,
            reference=user_conf,
        )

    # Assign random UUID to service to avoid clashes
    # We clip it because OSCAR only seems to support names smaller than 39 characters
    user_conf["name"] = f"ai4papi-{uuid.uuid1()}"[:39]

    # Add service owner
    user_conf["allowed_users"] = [auth_info["id"]]

    # Create service definition
    service_definition = make_service_definition(user_conf, vo)

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    _ = client.create_service(service_definition)

    return user_conf["name"]


@router.put("/services/{service_name}")
def update_service(
    vo: str,
    service_name: str,
    conf: Union[dict, None] = None,
    authorization=Depends(security),
):
    """
    Updates service if it exists.
    The method needs all service parameters to be on the request.
    """
    # Retrieve authenticated user info
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    # Load service configuration
    user_conf = deepcopy(papiconf.MODULES["user"]["values"])

    # Update values conf in case we received a submitted conf
    if conf is not None:
        user_conf = utils.update_values_conf(
            submitted=conf,
            reference=user_conf,
        )

    # Update conf values
    user_conf["name"] = service_name
    user_conf["allowed_users"] = [auth_info["id"]]

    # Create service definition
    service_definition = make_service_definition(user_conf, vo)

    # Update service
    client = get_client_from_auth(authorization.credentials, vo)
    _ = client.update_service(service_name, service_definition)

    return user_conf["name"]


@router.delete("/services/{service_uuid}")
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
