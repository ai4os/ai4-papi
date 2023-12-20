import copy
import csv
import os
from pathlib import Path

from cachetools import cached, TTLCache
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from ai4papi import auth
import ai4papi.conf as papiconf


router = APIRouter(
    prefix="/stats",
    tags=["Deployments stats"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def load_stats(
    namespace: str,
    ):

    main_dir = os.environ.get('ACCOUNTING_PTH', None)
    if not main_dir:
        raise HTTPException(
            status_code=500,
            detail="Deployments stats information not available (no env var).",
            )

    # Load all stats files
    stats = {}
    for name in ['full-agg', 'timeseries', 'users-agg']:
        pth = Path(main_dir) / 'summaries' / f'{namespace}-{name}.csv'

        if not pth.is_file():
            raise HTTPException(
                status_code=500,
                detail="Deployments stats information not available (missing file).",
                )

        with open(pth, 'r') as f:
            reader = csv.DictReader(f, delimiter=';')
            stats[name] = {k: [] for k in reader.fieldnames}
            for row in reader:
                for k, v in row.items():
                    if k not in ['date', 'owner']:
                        v= int(v)
                    stats[name][k].append(v)

    # Namespace aggregates are not lists
    stats['full-agg'] = {k: v[0] for k, v in stats['full-agg'].items()}

    return stats


@router.get("/")
def get_stats(
    vo: str,
    authorization=Depends(security),
    ):
    """
    Returns the following stats (per resource type):
    * the time-series usage of that VO
    * the aggregated usage of that VO
    * the aggregated usage of the user in that VO

    Parameters:
    * **vo**: Virtual Organization where you want the stats from.
    """

    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info['vos'])

    # Retrieve the associated namespace to that VO
    namespace = papiconf.MAIN_CONF['nomad']['namespaces'][vo]

    # Load proper namespace stats
    full_stats = load_stats(namespace=namespace)

    # Keep only stats from the current user
    user_stats = copy.deepcopy(full_stats)
    try:
        idx = full_stats['users-agg']['owner'].index(auth_info['id'])
        user_stats['users-agg'] = {k: v[idx] for k, v in full_stats['users-agg'].items()}
    except ValueError:  # user has still no recorded stats
        user_stats['users-agg'] = None

    return user_stats
