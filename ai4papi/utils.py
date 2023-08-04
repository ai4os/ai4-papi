"""
Miscellaneous utils
"""
import re

from fastapi import HTTPException
import requests


def generate_domain(
    hostname: str,
    base_domain: str,
    job_uuid: str,
    ):

    if hostname:  # user provided a hostname

        # Forbid some hostnames to avoid confusions
        if hostname.startswith('www') or hostname.startswith('http'):
            raise HTTPException(
                status_code=400,
                detail="Hostname should not start with `www` or `http`."
                )

        # Replace all non-alphanumerical characters from hostname with hyphens
        # to make it url safe
        hostname = re.sub('[^0-9a-zA-Z]', '-', hostname)

        # Check url safety
        if hostname.startswith('-'):
            raise HTTPException(
                status_code=400,
                detail="Hostname should start with alphanumerical character."
                )
        if hostname.endswith('-'):
            raise HTTPException(
                status_code=400,
                detail="Hostname should end with alphanumerical character."
                )
        if len(hostname) > 40:
            raise HTTPException(
                status_code=400,
                detail="Hostname should be shorter than 40 characters."
                )

        domain = f"{hostname}.{base_domain}"

    else:  # we use job_ID as default subdomain
        domain = f"{job_uuid}.{base_domain}"

    return domain


def check_domain(base_url):
    """
    Check if the domain is free so that we let user deploy to it.
    We have to check all possible services that could be hosted in that domain.

    Parameters:
    * **base_url**

    Returns None if the checks pass, otherwise raises an Exception.
    """
    s_names = [  # all possible services
        'deepaas',
        'ide',
        'monitor',
        'fedserver',
        ]
    s_urls = [f"http://{name}-{base_url}" for name in s_names]

    for url in s_urls:
        # First check if the URL is reachable
        try:
            r = requests.get(url)
        except requests.exceptions.ConnectionError:
            # URL was not reachable therefore assumed empty
            continue
        except Exception:
            # Other exception happened
            raise HTTPException(
            status_code=401,
            detail=f"We had troubles reaching {url}. Make sure it is a valid domain, "\
                    "otherwise contact with support."
            )

        # Domain still might be available if the error is a 404 coming **from Traefik**.
        # We have to check that the error 404 thrown by Traefik and not by some other
        # application. We do this by checking the headers.
        # This is a hacky fix for a limitation in Traefik:
        # https://github.com/traefik/traefik/issues/8141#issuecomment-844548035
        if r.status_code == 404:
            traefik_headers = {'Content-Type', 'X-Content-Type-Options', 'Date', 'Content-Length'}
            headers = set(dict(r.headers).keys())
            xcontent = r.headers.get('X-Content-Type-Options', None)
            lcontent = r.headers.get('Content-Length', None)

            if (headers == traefik_headers) and \
               (xcontent == 'nosniff') and \
               (lcontent == '19'):
                continue

        # In every other case, the URL is already in use.
        raise HTTPException(
            status_code=401,
            detail=f"The domain {url} seems to be taken. Please try again with a new domain or leave the field empty."
            )

    return None


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
