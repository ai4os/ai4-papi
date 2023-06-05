"""
Accounting of resources.
"""
from copy import deepcopy

from fastapi import HTTPException

from ai4papi.conf import USER_CONF


def check(
    user_conf:dict,
    vo: str,
    ):
    """
    Check the job configuration does not overflow the generic hardware limits.
    """
    user_conf = user_conf['hardware']  # user options
    ref = limit_resources(vo)  # generic quotas (vo-dependent)

    for k in ref.keys():
        if 'range' in ref[k].keys():
            if user_conf[k] < ref[k]['range'][0]:
                raise HTTPException(
                    status_code=400,
                    detail=f"The parameter {k} should bigger or equal to {ref[k]['range'][0]}."
                    )
            if user_conf[k] > ref[k]['range'][1]:
                raise HTTPException(
                    status_code=400,
                    detail=f"The parameter {k} should smaller or equal to {ref[k]['range'][1]}."
                    )


def limit_resources(
    vo: str,
    ):
    """
    Implement resource limits for specific users or VOs.
    """
    # Generate the conf
    conf = deepcopy(USER_CONF)['hardware']

    # Limit resources for tutorial users
    if vo == 'training.egi.eu':
        conf["cpu_num"]["range"] = [2, 4]
        conf["gpu_num"]["range"] = [0, 0]
        conf["gpu_num"]["description"] = "Tutorial users are not allowed to deploy on GPUs."
        conf["ram"]["range"] = [4000, 12000]
        conf["disk"]["value"] = 10000
        conf["disk"]["range"] = [5000, 10000]

    return conf
