"""
Manage OSCAR clusters to create and execute services.
"""
from copy import deepcopy
import json
from typing import List
import yaml

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPBearer
from oscar_python.client import Client
from pydantic import BaseModel, NonNegativeInt

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
        return Client(client_options)
    except Exception:
        raise Exception("Error creating OSCAR client")


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
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)
    r = client.get_cluster_info()
    return (r.status_code, json.loads(r.text))


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
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

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

    return (r.status_code, services)


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
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)
    result = client.get_service(service_name)
    return (result.status_code, json.loads(result.text))


@router.post("/services")
def create_service(
    vo: str,
    svc_conf: Service,
    authorization=Depends(security),
    ):
    """
    Creates a new inference service for an AI pre-trained model on a specific cluster
    """
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)

    # Create service
    service_definition, service_url = make_service_definition(svc_conf, vo)
    service_definition['allowed_users'] += [auth_info['id']]  # add service owner
    r = client.create_service(service_definition)
    if r.status_code == 201:
        return (r.status_code, service_url)
    else:
        return (r.status_code, None)


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
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)

    # Update service
    service_definition, service_url = make_service_definition(svc_conf, vo)
    service_definition['allowed_users'] += [auth_info['id']]  # add service owner
    r = client.update_service(svc_conf.name, service_definition)
    if r.status_code == 200:
        return (r.status_code, service_url)
    else:
        return (r.status_code, None)


@router.delete("/services/{service_name}")
def delete_service(
    vo: str,
    service_name: str,
    authorization=Depends(security),
    ):
    """
    Delete a specific service.
    """
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)
    r = client.remove_service(service_name)
    return (r.status_code, service_name)


@router.post("/services/{service_name}")
def inference(
    vo: str,
    service_name: str,
    data: dict,
    authorization=Depends(security),
    ):
    """
    Make a synchronous execution (inference)
    """
    auth_info = auth.get_user_info(authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    client = get_client_from_auth(authorization.credentials, vo)
    r = client.run_service(service_name, input=data["input_data"])
    try:
        return (r.status_code, json.loads(r.text))
    except Exception:
        return (r.status_code, r.text)
