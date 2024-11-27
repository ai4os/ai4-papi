"""
Accounting of resources.
"""

from copy import deepcopy

from fastapi import HTTPException

import ai4papi.conf as papiconf


def check_jobwise(
    conf: dict,
    vo: str,
):
    """
    Check the job configuration does not overflow the generic hardware limits.
    """
    # Retrieve generic quotas (vo-dependent)
    item_name = conf["general"]["docker_image"].split("/")[-1]
    ref = limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Compare with user options
    user_conf = conf["hardware"]
    for k in ref.keys():
        if "range" in ref[k].keys():
            if user_conf[k] < ref[k]["range"][0]:
                raise HTTPException(
                    status_code=400,
                    detail=f"The parameter {k} should bigger or equal to {ref[k]['range'][0]}.",
                )
            if user_conf[k] > ref[k]["range"][1]:
                raise HTTPException(
                    status_code=400,
                    detail=f"The parameter {k} should smaller or equal to {ref[k]['range'][1]}.",
                )


def check_userwise(
    conf: dict,
    deployments: dict,
):
    """
    Check the job configuration does not overflow the generic hardware limits.
    For example, a user cannot have more than two GPUs running/queued.
    """
    # Aggregate user resources
    user = {"gpu_num": 0}
    for d in deployments:
        user["gpu_num"] += d["resources"]["gpu_num"]

    # Check if aggregate is within the limits
    threshold = {"gpu_num": 2}
    if (user["gpu_num"] + conf["hardware"]["gpu_num"]) > threshold["gpu_num"] and conf[
        "hardware"
    ]["gpu_num"]:
        # TODO: remove this last line ("and conf['hardware']['gpu_num']"") once everyone
        # is within the quotas. For the time being this line is enabling users that have
        # overpassed the quotas (*) to make CPU deployments.
        # (*) before the quotas were in place
        raise HTTPException(
            status_code=400,
            detail="You already have at least 2 GPUs running and/or queued. "
            "If you want to make a new GPU deployment please delete one of your "
            "existing ones.",
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
        conf = deepcopy(papiconf.TOOLS[item_name]["user"]["full"])
    else:
        conf = deepcopy(papiconf.MODULES["user"]["full"])
    conf = conf["hardware"]

    # Limit resources for tutorial users
    if vo == "training.egi.eu":
        if "cpu_num" in conf.keys():
            conf["cpu_num"]["value"] = 2
            conf["cpu_num"]["range"] = [2, 4]
        if "gpu_num" in conf.keys():
            conf["gpu_num"]["range"] = [0, 0]
            conf["gpu_num"]["description"] = (
                "Tutorial users are not allowed to deploy on GPUs."
            )
        if "ram" in conf.keys():
            conf["ram"]["value"] = 2000
            conf["ram"]["range"] = [2000, 4000]
        if "disk" in conf.keys():
            conf["disk"]["value"] = 500
            conf["disk"]["range"] = [300, 1000]

    return conf
