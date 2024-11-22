"""
Miscellaneous utils
"""
from datetime import datetime
import os
import re

from cachetools import cached, TTLCache
from fastapi import HTTPException
import requests

import ai4papi.conf as papiconf


# Persistent requests session for faster requests
session = requests.Session()

# Retrieve tokens for better rate limit
github_token = os.environ.get('PAPI_GITHUB_TOKEN', None)


def update_values_conf(submitted, reference):
    """
    Update the reference YAML values configuration with a user submitted ones.
    We also check that the submitted conf has the appropriate keys.
    """
    for k in submitted.keys():

        # Check level 1 keys
        if k not in reference.keys():
            raise HTTPException(
                status_code=400,
                detail=f"The key `{k}` in not a valid parameter."
                )

        # Check level 2 keys
        s1 = set(submitted[k].keys())
        s2 = set(reference[k].keys())
        subs = s1.difference(s2)
        if subs:
            raise HTTPException(
                status_code=400,
                detail=f"The keys `{subs}` are not a valid parameters."
                )

        # Update with user values
        reference[k].update(submitted[k])

    return reference


def validate_conf(conf):
    """
    Validate user configuration
    """
    # Check that the Dockerhub image belongs either to "deephdc" or "ai4oshub"
    # or that it points to our Harbor instance (eg. CVAT)
    image = conf.get('general', {}).get('docker_image')
    if image:
        if image.split('/')[0] not in ["deephdc", "ai4oshub", "registry.services.ai4os.eu"]:
            raise HTTPException(
                status_code=400,
                detail="The docker image should belong to either 'deephdc' or 'ai4oshub' \
                DockerHub organizations or be hosted in the project's Harbor."
                )

    # Check datasets_info list
    datasets = conf.get('storage', {}).get('datasets')
    if datasets:
        for d in datasets:

            # Validate DOI and URL
            # ref: https://stackoverflow.com/a/48524047/18471590
            doiPattern = r"^10.\d{4,9}/[-._;()/:A-Z0-9]+$"
            urlPattern = r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
            if not (re.match(doiPattern, d['doi'], re.IGNORECASE) or re.match(urlPattern, d['doi'], re.IGNORECASE)):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid DOI or URL."
                    )

            # Check force pull parameter
            if not isinstance(d['force_pull'], bool):
                raise HTTPException(
                    status_code=400,
                    detail="Force pull should be bool."
                    )

    return conf


#TODO: temporarily parse every 24hrs (instead of 6hrs) to reduce a bit the latency
@cached(cache=TTLCache(maxsize=1024, ttl=24*60*60))
def get_github_info(owner, repo):
    """
    Retrieve information from a Github repo
    """
    # Avoid running this function if were are doing local development, because
    # repeatedly calling the Github API will otherwise get you blocked
    if papiconf.IS_DEV:
        print('[info] Skipping Github API info fetching (development).')
        return {}

    # Retrieve information from Github API
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {'Authorization': f'token {github_token}'} if github_token else {}
    r = session.get(url, headers=headers)

    # Parse the information
    out = {}
    if r.ok:
        repo_data = r.json()
        out['created'] = datetime.strptime(
            repo_data['created_at'],
            "%Y-%m-%dT%H:%M:%SZ",
            ).date().strftime("%Y-%m-%d")  # keep only the date
        out['updated'] = datetime.strptime(
            repo_data['updated_at'],
            "%Y-%m-%dT%H:%M:%SZ",
            ).date().strftime("%Y-%m-%d")
        out['license'] = (repo_data['license'] or {}).get('spdx_id', '')
        # out['stars'] = repo_data['stargazers_count']
    else:
        msg = "API rate limit exceeded" if r.status_code == 403 else ""
        print(f'  [Error] Failed to parse Github repo info: {msg}')

    return out
