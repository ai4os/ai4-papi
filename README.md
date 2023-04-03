<div align="center">
<img src="https://ai4eosc.eu/wp-content/uploads/sites/10/2022/09/horizontal-transparent.png" alt="logo" width="500"/>
</div>

# AI4EOSC library

> :warning: The library is under active development, so you might expect some breaking changes to happen. 

[//]: # ([![GitHub license]&#40;https://img.shields.io/github/license/ai4eosc/ai4eosc.svg&#41;]&#40;https://github.com/ai4eosc/ai4eosc/blob/master/LICENSE&#41;)
[//]: # ([![GitHub release]&#40;https://img.shields.io/github/release/ai4eosc/ai4eosc.svg&#41;]&#40;https://github.com/ai4eosc/ai4eosc/releases&#41;)
[//]: # ([![PyPI]&#40;https://img.shields.io/pypi/v/ai4eosc.svg&#41;]&#40;https://pypi.python.org/pypi/ai4eosc&#41;)
[//]: # ([![Python versions]&#40;https://img.shields.io/pypi/pyversions/ai4eosc.svg&#41;]&#40;https://pypi.python.org/pypi/ai4eosc&#41;)

This is a lightweight library for interacting with the AI4EOSC services. It aims at providing a stable UI, regardless of whether the underlying services change or not.

Its functionalities can also be accessed via an API (generated with [FastAPI](https://fastapi.tiangolo.com/)). 
This makes it possible for example to automate training launch and effectively decouples the Dashboard code from the underlying services we use (ie. Nomad).

> **TODO list** (in priority order):
> * (IISAS) implement authentication decorators to the functions (possibly using flaat?) (see [Authentication in FastAPI](https://fastapi.tiangolo.com/tutorial/security/) first)
> * (CSIC) if needed, create a database for trainings (instead of parsing Nomad) for better performance


## Installation

**Requirements**
To use this library you need to have [Nomad](https://developer.hashicorp.com/nomad/tutorials/get-started/get-started-install) installed to be able to interact with deployments.
Once you have Nomad installed you have to export the following variables:
```bash
export NOMAD_ADDR=https://publicip:4646
export NOMAD_CACERT=/path/to/tls/client-ca.crt
export NOMAD_CLIENT_CERT=/path/to/tls/client.crt
export NOMAD_CLIENT_KEY=/path/to/tls/client.key
```
For this you will need to ask the administrator of the cluster for the proper certificates.

Once you are done you can proceed to install the module:
```bash
pip install git+https://github.com/ai4eosc/ai4-lib.git
```

If you plan to use the module to develop, install instead in editable mode:
```bash
git clone https://github.com/ai4eosc/ai4-lib
cd ai4-lib
pip install -e .
```


## Usage

To deploy the API, run:

```bash
uvicorn main:app --reload
```

and go to http://127.0.0.1:8000/docs to check the API methods in the Swagger UI.

Here follows an overview of the available methods. The :lock: symbol indicates the method needs authentication to be accessed and :red_circle: methods that are planned but not implemented yet.

### Authentication

Some of the API methods are authenticated (:lock:) via OIDC tokens.

These are the steps to get a valid user token to access the methods:

1. Get a [DEEP IAM account](https://iam.deep-hybrid-datacloud.eu).
2. Install the [OIDC agent](https://github.com/indigo-dc/oidc-agent) in your system.
3. Configure the OIDC agent:
```bash
eval `oidc-agent-service start`
oidc-gen deep-iam
# - [2] https://iam.deep-hybrid-datacloud.eu/
# - Scopes: openid profile offline_access
# - login in browser
# - enter encryption password
```
4. Generate OIDC token
```bash
oidc-token deep-iam
# --> this will print your token
```

Now you are ready!

An authenticated curl call you look like the following:

```bash
curl --location 'http://localhost:8000' --header 'Authorization: Bearer <your-OIDC-token>'

```

<!-- #todo
* add command from Python script for faster debugging (eg. get_deployments(Request(header="...")))
* add EGI checkin when ready
For this you need to login in [EGI Check-In](https://aai.egi.eu/registry/) and enroll to one of the [supported Virtual Organizations (VO)](./etc/main_conf.yaml).
-->

### Exchange API

The Exchange API offers the possibility to interact with the metadata of the modules in the marketplace.

Methods:
* `GET(/modules)`: returns a list of all modules in the Marketplace
* `GET(/modules/summary)`: returns a list of all modules' basic metadata (name, title, summary, keywords)
* `GET(/modules/metadata/{module_name})`: returns the full metadata of a specific module
* `PUT(/modules/metadata/{module_name})`: :lock: :red_circle: updates the metadata of a specific module

**Notes**: The Exchange API returns results cached for up to 6 hours to improve UX (see [doctring](./ai4eosc/routers/modules.py)).

### Training API

The Training API offers the possibility to interact with the metadata of the modules in the marketplace.

Methods:
* `GET(/deployments)`: :lock: retrieve all deployments (with information) belonging to a user.
* `POST(/deployments)`: :lock: create a new deployment belonging to the user. 
* `DELETE(/deployments/{deployment_uuid})`: :lock: delete a deployment, users can only delete their own deployments.
* `GET(/info/conf)`: returns default configuration for creating a generic deployment.
* `GET(/info/conf/{module_name}`: returns the default configuration for creating a deployment for a specific module.

The functionalities can also be accessed without the API:

```python
from ai4eosc.routers import deployments

deployments.get_deployments()  # get all deployments
deployments.get_deployments(username='janedoe')  # get a specific user's deployments
#  Output example:
# [{'job_ID': 'example',
#   'status': 'running',
#   'owner': 'janedoe',
#   'submit_time': '2023-01-13 11:36:16',
#   'alloc_ID': 'e6b24722-e332-185a-a9b6-817ce8d26f48',
#   'resources': {'cpu_num': 2, 'gpu_num': 0, 'memoryMB': 8000, 'diskMB': 300},
#   'endpoints': {'deepaas': 'https://xxx.xxx.xxx.xxx:23143',
#    'monitor': 'https://xxx.xxx.xxx.xxx:22365',
#    'lab': 'https://xxx.xxx.xxx.xxx:20820'}}]

deployments.create_deployment(
    conf={
        'general':{
            'docker_image': 'deephdc/deep-oc-image-classification-tf:cpu',
            'service': 'deepaas'
        },
        'hardware': {
            'gpu': 1,
        }     
    },
    username='janedoe'
)
```


## Description

* `etc/main_conf.yaml`: Main configuration file of the API.
* `etc/userconf.yaml`: User customizable configuration to make a deployment in Nomad.
* `etc/job.nomad`: Additional non-customizable values (eg. ports)
