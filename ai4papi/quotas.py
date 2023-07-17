"""
Accounting of resources.
"""
from copy import deepcopy

from fastapi import HTTPException

import ai4papi.conf as papiconf


def check(
    conf: dict,
    vo: str,
    ):
    """
    Check the job configuration does not overflow the generic hardware limits.
    """
    # Retrieve generic quotas (vo-dependent)
    item_name = conf['general']['docker_image'].split('/')[-1]
    ref = limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Compare with user options
    user_conf = conf['hardware']
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
    item_name: str,
    vo: str,
    ):
    """
    Implement hardware limits for specific users or VOs.
    """
    # Select appropriate conf
    if item_name in papiconf.TOOLS.keys():
        conf = deepcopy(papiconf.TOOLS[item_name]['user']['full'])
    else:
        conf = deepcopy(papiconf.MODULES['user']['full'])
    conf = conf['hardware']

    # Limit resources for tutorial users
    if vo == 'training.egi.eu':
        if 'cpu_num' in conf.keys():
            conf["cpu_num"]["value"] = 2
            conf["cpu_num"]["range"] = [2, 4]
        if 'gpu_num' in conf.keys():
            conf["gpu_num"]["range"] = [0, 0]
            conf["gpu_num"]["description"] = "Tutorial users are not allowed to deploy on GPUs."
        if 'ram' in conf.keys():
            conf["ram"]["value"] = 2000
            conf["ram"]["range"] = [2000, 4000]
        if 'disk' in conf.keys():
            conf["disk"]["value"] = 500
            conf["disk"]["range"] = [300, 1000]

    return conf
