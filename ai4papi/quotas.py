"""
Accounting of resources.
"""

from fastapi import HTTPException

from ai4papi.conf import USER_CONF


def check(user_conf:dict):
    """
    Check the job configuration does not overflow the generic hardware limits.
    """
    user_conf = user_conf['hardware']  # user options
    ref = USER_CONF['hardware']  # generic quotas

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
