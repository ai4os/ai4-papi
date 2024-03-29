<div align="center">
  <img src="https://ai4eosc.eu/wp-content/uploads/sites/10/2022/09/horizontal-transparent.png" alt="logo" width="500"/>
</div>

# AI4EOSC - Platform API

> :warning: The library is under active development, so you might expect some breaking changes to happen.

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-%23FE5196?logo=conventionalcommits&logoColor=white)](https://conventionalcommits.org)

[//]: # ([![GitHub license]&#40;https://img.shields.io/github/license/ai4papi/ai4papi.svg&#41;]&#40;https://github.com/ai4papi/ai4papi/blob/master/LICENSE&#41;)
[//]: # ([![GitHub release]&#40;https://img.shields.io/github/release/ai4papi/ai4papi.svg&#41;]&#40;https://github.com/ai4papi/ai4papi/releases&#41;)
[//]: # ([![PyPI]&#40;https://img.shields.io/pypi/v/ai4papi.svg&#41;]&#40;https://pypi.python.org/pypi/ai4papi&#41;)
[//]: # ([![Python versions]&#40;https://img.shields.io/pypi/pyversions/ai4papi.svg&#41;]&#40;https://pypi.python.org/pypi/ai4papi&#41;)

This is the Platform API for interacting with the AI4EOSC services, built using
[FastAPI](https://fastapi.tiangolo.com/).
It aims at providing a stable UI, effectively decoupling the services offered by the
project from the underlying tools we use to provide them (ie. Nomad).

The API is currently deployed here:

* [production API](https://api.cloud.ai4eosc.eu/docs) (`master` branch)
* [development API](https://api.dev.ai4eosc.eu/docs) (`dev` branch)

Images of both API are accessible in [our private Harbor](https://registry.services.ai4os.eu/ai4os/ai4-papi) and [Dockerhub](https://hub.docker.com/repository/docker/ignacioheredia/ai4-papi/).
<!---
TODO: replace with AI4EOSC Dockerhub account
-->

The Dashboards pointing to those APIs are respectively:

* [production Dashboard](https://dashboard.cloud.ai4eosc.eu)
* [development Dashboard](https://dashboard.dev.ai4eosc.eu)

## Installation

**Requirements**
To use this library you need to have
[Nomad](https://developer.hashicorp.com/nomad/tutorials/get-started/get-started-install)
installed to be able to interact with deployments.
Once you have Nomad installed you have to export the following variables with the proper
local paths and http address:
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

To deploy the API, the are several options:

1. Using entrypoints:
```bash
ai4papi-run --host 0.0.0.0 --port 8080
```

2. Using uvicorn directly (with the auto `reload` feature enabled if you are developing):
```bash
uvicorn ai4papi.main:app --reload
```

3. Using our [Makefile](./Makefile)

4. From Dockerhub

```bash
docker run  -v /local-path-to/nomad-certs:/home/nomad-certs -p 8080:80 ignacioheredia/ai4-papi:prod
```

5. Building from our [Dockerfile](./docker/Dockerfile).

```bash
docker build -t ai4-papi:prod --build-arg papi_branch=master .
docker run -v /local-path-to/nomad-certs:/home/nomad-certs -p 8080:80 ai4-papi:prod
```

Once the API is running, go to http://127.0.0.1:8080/docs to check the API methods in the
Swagger UI.


### Authentication

Some of the API methods are authenticated (:lock:) via OIDC tokens, so you will need to
perform the following steps to access those methods.

#### Configure the OIDC provider

1. Create an [EGI Check-In](https://aai.egi.eu/registry/) account.
2. Enroll (`People > Enroll`) in one of the approved Virtual Organizations:
    - `vo.ai4eosc.eu`
    - `vo.imagine-ai.eu`

You will have to wait until an administrator approves your request before proceeding
with the next steps.
Supported OIDC providers and Virtual Organizations are described in the
[configuration](./etc/main_conf.yaml).

#### Generating a valid refresh token

There are two ways of generating a valid refresh user token to access the methods:
either via an UI or via the terminal.

##### Generate a token with a UI

If have a EGI Check-In account, you can generate a refresh user token with
[EGI token](https://aai.egi.eu/token): click `Authorise` and sign-in with your account.
Then use the `Access Token` to authenticate your calls.

##### Generate a token via the terminal

1. Install the [OIDC agent](https://github.com/indigo-dc/oidc-agent) in your system.

2. Configure the OIDC agent:
```bash
eval `oidc-agent-service start`
oidc-gen \
  --issuer https://aai.egi.eu/auth/realms/egi \
  --scope "openid profile offline_access eduperson_entitlement email" \
  egi-checkin
```
It will open the browser so you can authenticate with your EGI account. Then go back to
the terminal and finish by setting and encryption password.

3. Add the following line to your `.bashrc` to start the agent automatically at startup
([ref](https://github.com/indigo-dc/oidc-agent/issues/489#issuecomment-1472189510)):
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
* From in the Swagger UI (http://localhost:8080/docs), click in the upper right corner
button `Authorize` :unlock: and input your token. From now on you will be authenticated
when making API calls from the Swagger UI.
* From inside a Python script:
```python
from types import SimpleNamespace
from ai4papi.routers.v1 import deployments

deployments.get_deployments(
    vos=['vo.ai4eosc.eu'],
    authorization=SimpleNamespace(
        credentials='your-OIDC-token'
    ),
)
```

#### API methods

Here follows an overview of the available methods. The :lock: symbol indicates the
method needs authentication to be accessed and :red_circle: methods that are planned
but not implemented yet.

Same methods are also valid for `tools` instead of `modules`.

#### Exchange API

The Exchange API offers the possibility to interact with the metadata of the modules in
the marketplace.

Methods:
* `GET(/v1/catalog/modules)`: returns a list of all modules in the Marketplace
* `GET(/v1/catalog/modules/detail)`: returns a list of all modules' basic metadata (name, title,
summary, keywords)
 `GET(/v1/catalog/modules/tags)`: returns a list of all modules' tags
* `GET(/v1/catalog/modules/{item_name}/config)`: returns the default configuration for creating a
deployment for a specific module
* `GET(/v1/catalog/modules/{item_name}/metadata)`: returns the full metadata of a specific module

**Notes**: The Exchange API returns results cached for up to 6 hours to improve UX (see
[doctring](./ai4papi/routers/v1/modules.py)).

#### Training API

The Training API offers the possibility to interact with the metadata of the modules in
the marketplace.

Methods:
* `GET(/v1/deployments/modules)`: :lock: retrieve all deployments (with information) belonging to a
user.
* `POST(/v1/deployments/modules)`: :lock: create a new deployment belonging to the user.
* `GET(/v1/deployments/modules/{deployment_uuid})`: :lock: retrieve info of a single deployment belonging to a user
* `DELETE(/v1/deployments/modules/{deployment_uuid})`: :lock: delete a deployment, users can only
delete their own deployments.


The functionalities can also be accessed without the API:

```python
from types import SimpleNamespace

from ai4papi.routers.v1 import deployments


# Get all the user's deployments
deployments.modules.get_deployments(
    vos=['vo.ai4eosc.eu'],
    authorization=SimpleNamespace(
        credentials='your-OIDC-token'
    ),
)
# [{'job_ID': 'example',
#   'status': 'running',
#   'owner': '4545898984949741@someprovider',
#   'submit_time': '2023-01-13 11:36:16',
#   'alloc_ID': 'e6b24722-e332-185a-a9b6-817ce8d26f48',
#   'resources': {
#       'cpu_num': 2,
#       'gpu_num': 0,
#       'memoryMB': 8000,
#       'diskMB': 300
#   },
#   'endpoints': {
#       'deepaas': 'https://deepaas.xxx.xxx.xxx.xxx',
#       'monitor': 'https://monitor.xxx.xxx.xxx.xxx',
#       'ide': 'https://ide.xxx.xxx.xxx'
#   }
# }]
```


## Description

* `etc/main_conf.yaml`: main configuration file of the API
* `etc/modules`: configuration files for standard modules
* `etc/tools`: configuration files for tools
  - `deep-oc-federated-server`: federated server

The pattern for the subfolders follows:
  - `user.yaml`: user customizable configuration to make a deployment in Nomad.
    Also contains the generic quotas for hardware (see `range` parameter).
  - `nomad.hcl`: additional non-customizable values (eg. ports)
