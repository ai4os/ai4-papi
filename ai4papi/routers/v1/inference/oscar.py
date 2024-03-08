"""
Manage OSCAR clusters to create and execute services.

"""
from oscar_python.client import Client
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.security import HTTPBearer
from typing import Tuple, Union
from ai4papi import auth, utils
import json
import re
from ai4papi.conf import MAIN_CONF
from pydantic import BaseModel

router = APIRouter(
    prefix="/inference",
    tags=["inference"],
    responses={404: {"description": "Inference not found"}},
)

app = FastAPI()

class Service(BaseModel):
    name: str
    memory: str
    cpu: str
    image: str
    input_type: str
    allowed_users: list[str]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {            
                    "name": "string",
                    "memory": "string",
                    "cpu": "string",
                    "image": "string",
                    "input_type": "string",
                    "output_type": "string",
                    "allowed_users": ["string"]
                }
            ]
        }
    }

security = HTTPBearer()

def get_client_from_auth(authorization, cluster_id, vo):
    # Retrieve authenticated user info
    oidc_token = authorization.credentials
    auth_info = auth.get_user_info(token=oidc_token)
    auth.check_vo_membership(vo, auth_info['vos'])

    try:
        endpoint = MAIN_CONF["oscar"]["clusters"][cluster_id]["endpoint"]
    except:
        raise HTTPException(
                status_code=400,
                detail=f"The provided cluster id '{cluster_id}' is not listed on the configuration file"
        )

    client_options = {
                    'cluster_id': cluster_id,
                    'endpoint': endpoint,
                    'oidc_token': oidc_token,
                    'ssl': 'true'}

    try:
        return Client(client_options)
    except:
        raise Exception("Error creating OSCAR client")

def make_service_definition(svc_conf, cluster_id):

    service_definition = utils.get_service_base_definition()
    service_definition["name"] = svc_conf.name
    service_definition["memory"] = svc_conf.memory
    service_definition["cpu"] = svc_conf.cpu
    service_definition["image"] = svc_conf.image
    service_definition["script"] = re.sub('.type','.'+svc_conf.input_type, service_definition["script"])
    service_definition["allowed_users"] = svc_conf.allowed_users

    endpoint = MAIN_CONF["oscar"]["clusters"][cluster_id]["endpoint"]
    service_url = endpoint+"/services/"+cluster_id+"/"+svc_conf.name

    return service_definition, service_url

# Gets information about the cluster.
#  - Returns a JSON with the cluster information.
@router.get("/cluster/{cluster_id}")
def get_cluster_info(cluster_id: str,
                        vo: str,
                        authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)
    result = client.get_cluster_info()
    return (result.status_code, json.loads(result.text))


# Creates a new inference service for an AI pre-trained model on a specific cluster
@router.post("/services/{cluster_id}")
def create_service(cluster_id: str,
                    vo: str,
                    svc_conf: Service,
                    authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)

    # If authentication doesn't fail set user vo on the service
    service_definition, service_url = make_service_definition(svc_conf, cluster_id)
    service_definition["vo"] = vo

    result = client.create_service(service_definition)
    if result.status_code == 201:
        return (result.status_code, service_url)
    else:
        return (result.status_code, None)


# Updates service if it exists
@router.put("/services/{cluster_id}/{service_name}")
def update_service(cluster_id: str,
                    vo: str,
                    svc_conf: Service,
                    authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)

    # If authentication doesn't fail set user vo on the service
    service_definition, service_url = make_service_definition(svc_conf, cluster_id)
    service_definition["vo"] = vo
    service_definition["allowed_users"] = svc_conf.allowed_users

    # update_service method needs all service parameters to be on the request
    result = client.update_service(svc_conf.name, service_definition)
    if result.status_code == 200:
        return (result.status_code, service_url)
    else:
        return (result.status_code, None)

# Retrieves a list of all the deployed services of the cluster.
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}")
def get_services_list(cluster_id: str,
                        vo: str,
                        authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)
    result = client.list_services()

    return (result.status_code, json.loads(result.text))

# Retrieves a specific service.
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}/{service_name}")
def get_service(cluster_id: str,
                vo: str,
                service_name: str,
                authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)
    result = client.get_service(service_name)

    return (result.status_code, json.loads(result.text))

# Delete a specific service.
@router.delete("/services/{cluster_id}/{service_name}")
def delete_service(cluster_id: str,
                    vo: str,
                    service_name: str,
                    authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id, vo)

    result = client.remove_service(service_name)
    return (result.status_code,service_name)

#Make a synchronous execution (inference)
@router.post("/services/{cluster_id}/{service_name}")
def inference(cluster_id: str,
                vo: str,
                service_name: str,
                data: dict,
                authorization=Depends(security)):
    client = get_client_from_auth(authorization, cluster_id, vo)

    result = client.run_service(service_name, input=data["input_data"])
    try:
        return (result.status_code, json.loads(result.text))
    except:
        return (result.status_code, result.text)
