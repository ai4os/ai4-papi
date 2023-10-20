"""
Manage OSCAR clusters to create and execute services.

"""
from oscar_python.client import Client
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from typing import Tuple, Union
from ai4papi import utils
import json
from ai4papi.conf import MAIN_CONF
from pydantic import BaseModel

router = APIRouter(
    prefix="/inference",
    tags=["inference"],
    responses={404: {"description": "Inference not found"}},
)


class Service(BaseModel):
    cluster_id: str
    name: str
    memory: str
    cpu: str
    image: str


security = HTTPBearer()

def get_client_from_auth(auth, cluster_id):
    # Retrieve authenticated user info
    oidc_token = auth.credentials
    endpoint = MAIN_CONF["oscar"]["clusters"][cluster_id]["endpoint"]

    client_options = {
                    'cluster_id': cluster_id,
                    'endpoint': endpoint,
                    'oidc_token': oidc_token,
                    'ssl': 'true'}
    
    try:
        return Client(client_options)
    except:
        raise Exception("Error creating OSCAR client")

# Gets information about the cluster. 
#  - Returns a JSON with the cluster information.
@router.get("/cluster/{cluster_id}")
def get_cluster_info( cluster_id: str,
                      authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id)
    result = client.get_cluster_info()
    return json.loads(result.text)


# Creates a new inference service for an AI pre-trained model on a specific cluster 
@router.post("/services/{cluster_id}")
def create_service(
                   cluster_id: str, 
                   svc_conf: Service,
                   authorization=Depends(security),
                   ):
    
    if svc_conf is None:
        raise HTTPException(
                status_code=400,
                detail=f"Service configuration not found."
        )
    
    service_definition = utils.get_service_base_definition()
    service_definition["name"] = svc_conf.name
    service_definition["memory"] = svc_conf.memory
    service_definition["cpu"] = svc_conf.cpu
    service_definition["image"] = svc_conf.image

    #TODO return {“service_url”: “string”}
    client = get_client_from_auth(authorization, cluster_id)
    result = client.create_service(json.dumps(service_definition))
    return result.text
    
#TODO FIX
# Updates service if it exists
@router.put("/services/{cluster_id}/{service_name}")
def update_service(
                   cluster_id: str, 
                   svc_conf: Service,
                   authorization=Depends(security),
                   ):
    
    # Needs all parameters to be on the request
    service_definition = utils.get_service_base_definition()
    service_definition["name"] = svc_conf.name
    service_definition["memory"] = svc_conf.memory
    service_definition["cpu"] = svc_conf.cpu
    service_definition["image"] = svc_conf.image

    #TODO return {“service_url”: “string”}
    client = get_client_from_auth(authorization, cluster_id)
    client.update_service(service_definition)

# Retrieves a list of all the deployed services of the cluster. 
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}")
def get_services_list(cluster_id: str,
                      authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id)
    result = client.list_services()
    return json.loads(result.text)

# Retrieves a specific service.
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}/{service_name}")
def get_service(cluster_id: str,
                service_name: str,
                authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id)
    result = client.get_service(service_name)
    return json.loads(result.text)

# Delete a specific service.
@router.delete("/services/{cluster_id}/{service_name}")
def delete_service(cluster_id: str,
                service_name: str,
                authorization=Depends(security)):

    client = get_client_from_auth(authorization, cluster_id)
    result = client.delete_service(service_name)
    return result

#Make a synchronous execution (inference)
@router.post("/services/{cluster_id}/{service_name}")
def inference(  cluster_id: str, 
                service_name: str,
                data: dict,
                authorization=Depends(security),
            ):
    client = get_client_from_auth(authorization, cluster_id)
    result = client.run_service(service_name, input=data["input_data"])
    try:
        return json.loads(result.text)
    except:
        return result.text
