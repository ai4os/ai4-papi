"""
Parse Zenodo API and return results as is.

We go through PAPI (ie. not query Zenodo directly from the Dashboard) because we want to
make *authenticated* calls with Zenodo. And we cannot have a Zenodo token in the Dashboard
because the calls are being run on the client side (ie. the client would see the Zenodo
token).
"""

import os
import requests
import time

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException

import ai4papi.conf as papiconf


router = APIRouter(
    prefix="/zenodo",
    tags=["Zenodo datasets"],
    responses={404: {"description": "Not found"}},
)


API_URL = 'https://zenodo.org'

session = requests.Session()

# If available, authenticate the call to Zenodo to increase rate limit.
# https://developers.zenodo.org/#rate-limiting
zenodo_token = os.environ.get('ZENODO_TOKEN', None)
if zenodo_token:
    session.headers = {
        'Authorization': f'Bearer {zenodo_token}',
    }


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_dataset_versions(
    _id: str,
    ):
    """
    Retrieve all versions from a concept_id.

    Parameters
    ----------
    * _id: Zenodo ID (ie. ID that represents all versions).
      Has to be a non-conceptual ID, otherwise the GET fails.
    """

    r = session.get(f"{API_URL}/api/records/{_id}/versions")

    if not r.ok:
        raise HTTPException(
            status_code=500,
            detail="Failed to query Zenodo (versions).",
            )

    versions = []
    for record in r.json()['hits']['hits']:
        versions.append({
            'version': record['metadata']['version'],
            'title': record['title'],
            'doi': record['doi'],
            'latest': record['metadata']['relations']['version'][0]['is_last'],
            })

    return versions


@router.get("/")
@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_datasets(
    vo: str,
    versions: bool = True,
    ):
    """
    Returns a list of all datasets belonging for a given Zenodo community.

    For each record, we return the ID and the DOI of the conceptual record (the
    record that is used to reference *all* versions)

    Parameters
    ----------
    * **vo**: Virtual Organization to maps to a Zenodo community
    * **versions**: Whether to include additional versions in the output
    """
    # Build base query
    community = papiconf.MAIN_CONF['zenodo']['communities'][vo]
    url = f"{API_URL}/api/communities/{community}/records"
    params = {'q': 'resource_type.type:dataset'}

    # Iterate over results
    datasets = []
    while True:
        r = session.get(url, params=params)

        if not r.ok:
            raise HTTPException(
                status_code=500,
                detail="Failed to query Zenodo (datasets).",
                )

        r = r.json()
        for record in r['hits']['hits']:
            datasets.append({
                'title': record['title'],
                'id': record['id'],
                'doi': record['doi'],
                })

            if versions:
                # Sleep for 1s to avoid reaching 60 queries per minute limit
                # https://developers.zenodo.org/#rate-limiting
                time.sleep(1)
                datasets[-1]['versions'] = get_dataset_versions(record['id'])

        if 'next' in r['links']:
            url =  r['links']['next']
            time.sleep(1)
        else:
            break

    return datasets
