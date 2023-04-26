<div align="center">
<img src="https://ai4eosc.eu/wp-content/uploads/sites/10/2022/09/horizontal-transparent.png" alt="logo" width="500"/>
</div>

# AI4EOSC - Platform API

> :warning: The library is under active development, so you might expect some breaking changes to happen. 

[//]: # ([![GitHub license]&#40;https://img.shields.io/github/license/ai4papi/ai4papi.svg&#41;]&#40;https://github.com/ai4papi/ai4papi/blob/master/LICENSE&#41;)
[//]: # ([![GitHub release]&#40;https://img.shields.io/github/release/ai4papi/ai4papi.svg&#41;]&#40;https://github.com/ai4papi/ai4papi/releases&#41;)
[//]: # ([![PyPI]&#40;https://img.shields.io/pypi/v/ai4papi.svg&#41;]&#40;https://pypi.python.org/pypi/ai4papi&#41;)
[//]: # ([![Python versions]&#40;https://img.shields.io/pypi/pyversions/ai4papi.svg&#41;]&#40;https://pypi.python.org/pypi/ai4papi&#41;)

This is the Platform API for interacting with the AI4EOSC services, built using [FastAPI](https://fastapi.tiangolo.com/). 
It aims at providing a stable UI, effectively decoupling the services offered by the project from the underlying tools we use to provide them (ie. Nomad).

The API is currently [deployed here](https://api.dev.ai4eosc.eu/docs).

## Installation

**Requirements**
To use this library you need to have [Nomad](https://developer.hashicorp.com/nomad/tutorials/get-started/get-started-install) installed to be able to interact with deployments.
Once you have Nomad installed you have to export the following variables with the proper local paths and http address:
```bash
export NOMAD_ADDR=https://some-public-ip:4646
export NOMAD_CACERT=/path/to/tls/nomad-ca.pem
export NOMAD_CLIENT_CERT=/path/to/tls/nomad-cli.pem
export NOMAD_CLIENT_KEY=/path/to/tls/nomad-cli-key.pem
```
For this you will need to ask the administrator of the cluster for the proper certificates.

Once you are done you can proceed to install the module:
```bash
pip install git+https://github.com/ai4eosc/ai4-papi.git
```

If you plan to use the module to develop, install instead in editable mode:
```bash
git clone https://github.com/ai4eosc/ai4-papi
cd ai4-papi
pip install -e .
```


## Usage

To deploy the API, run:

```bash
ai4papi-run --host 0.0.0.0 --port 8080
```

and go to http://127.0.0.1:8080/docs to check the API methods in the Swagger UI.

If your are developing the API, you might want to run using uvicorn's auto `reload` feature:

```bash
uvicorn main:app --reload
```

We also provide a [Dockerfile](./docker/Dockerfile) to run the API with SSL.

Here follows an overview of the available methods. The :lock: symbol indicates the method needs authentication to be accessed and :red_circle: methods that are planned but not implemented yet.

### Authentication

Some of the API methods are authenticated (:lock:) via OIDC tokens, so you will need to perform the following steps to access those methods.

#### Configure the OIDC provider

1. Create an [EGI Check-In](https://aai.egi.eu/registry/) account.
2. Enroll (`People > Enroll`) in one of the approved Virtual Organizations: 
    - `vo.ai4eosc.eu`
    - `vo.imagine-ai.eu`
    
You will have to wait until an administrator approves your request before proceeding with the next steps. 
Supported OIDC providers and Virtual Organizations are described in the [configuration](./etc/main_conf.yaml).

#### Generating a valid refresh token

There are two ways of generating a valid refresh user token to access the methods: either via an UI or via the terminal.

##### Generate a token with a UI

If have a EGI Check-In account, you can generate a refresh user token with [EGI token](https://aai.egi.eu/token): click `Authorise` and sign-in with your account. Then use the `Access Token` to authenticate your calls.

##### Generate a token via the terminal

1. Install the [OIDC agent](https://github.com/indigo-dc/oidc-agent) in your system.

2. Configure the OIDC agent:
```bash
eval `oidc-agent-service start`
oidc-gen \
  --issuer https://aai.egi.eu/auth/realms/egi \
  --scope "openid profile offline_access eduperson_entitlement" \
  egi-checkin
```
It will open the browser so you can authenticate with your EGI account. Then go back to the terminal and finish by setting and encryption password.

3. Add the following line to your `.bashrc` to start the agent automatically at startup ([ref](https://github.com/indigo-dc/oidc-agent/issues/489#issuecomment-1472189510)):
```bash
eval `oidc-agent-service use` > /dev/null
```

4. Generate the OIDC token
```bash
oidc-token egi-checkin
```

5. `Optional`: You can check you have set everything up correctly by running:
```bash
flaat-userinfo --oidc-agent-account egi-checkin
```
This should print you EGI user information.

#### Making authenticated calls

To make authenticated calls:
* An authenticated CURL call looks like the following:
```bash
curl --location 'http://localhost:8080' --header 'Authorization: Bearer <your-OIDC-token>'
```
* From in the Swagger UI (http://localhost:8080/docs), click in the upper right corner button `Authorize` :unlock: and input your token. From now on you will be authenticated when making API calls from the Swagger UI.
* From inside a Python script:
```python
from types import SimpleNamespace
from ai4papi.routers.v1 import deployments

deployments.get_deployments(
    authorization=SimpleNamespace(
        credentials='your-OIDC-token'
    ),
)
```


### Exchange API

The Exchange API offers the possibility to interact with the metadata of the modules in the marketplace.

Methods:
* `GET(/modules)`: returns a list of all modules in the Marketplace
* `GET(/modules/summary)`: returns a list of all modules' basic metadata (name, title, summary, keywords)
* `GET(/modules/metadata/{module_name})`: returns the full metadata of a specific module
* `PUT(/modules/metadata/{module_name})`: :lock: :red_circle: updates the metadata of a specific module

**Notes**: The Exchange API returns results cached for up to 6 hours to improve UX (see [doctring](./ai4papi/routers/v1/modules.py)).

### Training API

The Training API offers the possibility to interact with the metadata of the modules in the marketplace.

Methods:
* `GET(/deployments)`: :lock: retrieve all deployments (with information) belonging to a user.
* `POST(/deployments)`: :lock: create a new deployment belonging to the user. 
* `DELETE(/deployments/{deployment_uuid})`: :lock: delete a deployment, users can only delete their own deployments.
* `GET(/info/conf/{module_name}`: returns the default configuration for creating a deployment for a specific module.

The functionalities can also be accessed without the API:

```python
from types import SimpleNamespace

from ai4papi.routers.v1 import deployments


# Get all the user's deployments
deployments.get_deployments(
    authorization=SimpleNamespace(
        credentials='your-OIDC-token'
    ),
)
# [{'job_ID': 'example',
#   'status': 'running',
#   'owner': '4545898984949741@someprovider',
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
    authorization=SimpleNamespace(
        credentials='your-OIDC-token'
    ),
)
```


## Description

* `etc/main_conf.yaml`: Main configuration file of the API.
* `etc/userconf.yaml`: User customizable configuration to make a deployment in Nomad.
* `etc/job.nomad`: Additional non-customizable values (eg. ports)
