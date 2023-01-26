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
> * (CSIC) add additional functionalities (module marketplace creation, module listing, etc)
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


## Usage

Currently, only basic functionalities for interacting with the deployments are implemented.

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
#   'resources': {'cpu_num': 2, 'memoryMB': 8000, 'diskMB': 300},
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

You can also run it as a server to make calls via the API:

```bash
uvicorn main:app --reload
```

and go to http://127.0.0.1:8000/docs to check the API methods in the Swagger UI.


## Description

* `etc/userconf.yaml`: User customizable configuration to make a deployment in Nomad.
* `etc/job.nomad`: Additional non-customizable values (eg. ports)
