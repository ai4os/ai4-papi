"""
Manage OSCAR clusters to create and execute services.

"""
from oscar_python.client import Client
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from typing import Tuple, Union
from ai4papi.auth import get_user_info
from ai4papi import utils
import json

router = APIRouter(
    prefix="/inference",
    tags=["inference"],
    responses={404: {"description": "Inference not found"}},
)


security = HTTPBearer()
ROUTE = ""
USER_CLUSTERS = {}
USER_TOKEN = ''

def get_client_from_auth(auth, conf):
    # Retrieve authenticated user info
    oidc_token = auth.credentials
    
    client_options = {
                    'cluster_id':conf["cluster_id"],
                    'endpoint': conf["endpoint"],
                    'oidc_token': oidc_token,
                    'ssl': 'true'}
    
    try:
        return Client(client_options)
    except:
        raise Exception("Error creating OSCAR client")

def get_client_from_id(auth, cluster_id):

    received_token = auth.credentials
    stored_token = None
    if cluster_id not in USER_CLUSTERS:
        raise HTTPException(
        status_code=400,
        detail=f"Cluster '{cluster_id}' not found"
    )

    cluster = json.loads(USER_CLUSTERS[cluster_id])
    stored_token = cluster["oidc_token"]

    if received_token != stored_token:
        raise HTTPException(
        status_code=401,
        detail=f"Invalid authentication."            
        )
    
    client_options = json.loads(USER_CLUSTERS[cluster_id])
    try:
        return Client(client_options)
    except:
        raise Exception("Error creating OSCAR client")
    
def check_cluster_exists(cluster_id):
    if cluster_id not in USER_CLUSTERS:
        raise HTTPException(
        status_code=400,
        detail=f"Cluster '{cluster_id}' not found"
    )     

# Register a new cluster for the user that performed the invocation.
@router.post("/cluster")
def register_cluster(authorization=Depends(security),
                     conf: Union[dict, None] = None):

    cluster_id = conf["cluster_id"]
    if cluster_id in USER_CLUSTERS:
        raise HTTPException(
            status_code=400,
            detail=f"A cluster with id '{cluster_id}' is already registered"
        )
    
    conf["oidc_token"] = authorization.credentials
    USER_CLUSTERS[cluster_id] = json.dumps(conf)
    return json.loads(USER_CLUSTERS[cluster_id])

# List all the registered clusters for the actual user.
@router.get("/cluster")
def list_registered_clusters(authorization=Depends(security)):
    auth_clusters = {}
    token = authorization.credentials
    for cluster_id in USER_CLUSTERS:
        cluster = json.loads(USER_CLUSTERS[cluster_id])
        if cluster.get("oidc_token")== token:
            auth_clusters[cluster_id] = cluster
    
    return auth_clusters

# Gets information about the cluster. 
#  - Returns a JSON with the cluster information.
@router.get("/cluster/{cluster_id}")
def get_cluster_info( cluster_id: str,
                      authorization=Depends(security)):
    
    check_cluster_exists(cluster_id)

    client = get_client_from_id(authorization, cluster_id)
    result = client.get_cluster_info()
    return json.loads(result.text)


# Deregister a cluster 
@router.delete("/cluster/{cluster_id}")
def deregister_cluster( cluster_id: str,
                        authorization=Depends(security)):
    received_token = authorization.credentials
    stored_token = None

    check_cluster_exists(cluster_id)

    cluster = json.loads(USER_CLUSTERS[cluster_id])
    stored_token = cluster["oidc_token"]

    if received_token != stored_token:
        raise HTTPException(
        status_code=401,
        detail=f"Invalid authentication."            
        )
    
    deleted = USER_CLUSTERS.pop(cluster_id)
    return json.loads(deleted)

# Creates a new inference service for an AI pre-trained model on a specific cluster 
@router.post("/services/{cluster_id}")
def create_service(
                   cluster_id: str, 
                   authorization=Depends(security),
                   conf: Union[dict, None] = None
                   ):
    
    service_definition = utils.get_service_base_definition()
    service_definition["name"] = conf["name"]
    service_definition["memory"] = conf["memory"]
    service_definition["cpu"] = conf["cpu"]
    service_definition["image"] = conf["image"]
    
    check_cluster_exists(cluster_id)
    #TODO return {“service_url”: “string”}

    client = get_client_from_id(authorization, conf["cluster_id"])
    client.create_service(service_definition)
    

# Updates service if it exists
@router.put("/services/{cluster_id}/{service_name}")
def update_service(
                   cluster_id: str, 
                   authorization=Depends(security),
                   conf: Union[dict, None] = None
                   ):
    
    # Needs all parameters to be on the request
    service_definition = utils.get_service_base_definition()
    service_definition["name"] = conf["name"]
    service_definition["memory"] = conf["memory"]
    service_definition["cpu"] = conf["cpu"]
    service_definition["image"] = conf["image"]
    
    check_cluster_exists(cluster_id)
    #TODO return {“service_url”: “string”}

    client = get_client_from_id(authorization, conf["cluster_id"])
    client.update_service(service_definition)

# Retrieves a list of all the deployed services of the cluster. 
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}")
def get_services_list(cluster_id: str,
                      authorization=Depends(security)):

    check_cluster_exists(cluster_id)

    client = get_client_from_id(authorization, cluster_id)
    result = client.list_services()
    return result, json.loads(result.text)

# Retrieves a specific service.
#  - Returns a JSON with the cluster information.
@router.get("/services/{cluster_id}/{service_name}")
def get_service(cluster_id: str,
                service_name: str,
                authorization=Depends(security)):
    
    check_cluster_exists(cluster_id)

    client = get_client_from_id(authorization, cluster_id)
    result = client.get_service(service_name)
    return result, json.loads(result.text)

# Delete a specific service.
@router.delete("/services/{cluster_id}/{service_name}")
def delete_service(cluster_id: str,
                service_name: str,
                authorization=Depends(security)):

    check_cluster_exists(cluster_id)

    client = get_client_from_id(authorization, cluster_id)
    result = client.delete_service(service_name)
    return result

#Make a synchronous execution (inference)
@router.post("/services/{cluster_id}/{service_name}")
def inference(  cluster_id: str, 
                service_name: str,
                authorization=Depends(security),
                conf: Union[dict, None] = None
            ):
    
    check_cluster_exists(cluster_id)

    client = get_client_from_id(authorization, cluster_id)
    result = client.run_service(service_name, input=conf["input_data"])
    return json.loads(result.text)