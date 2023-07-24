"""
Miscellaneous utils
"""
import urllib

from fastapi import HTTPException
import requests


def generate_domain(
    hostname: str,
    base_domain: str,
    job_uuid: str,
    ):
    if hostname:
        if '.' in hostname:  # user provide full domain
            if not hostname.startswith('http'):
                hname = f'http://{hostname}'
            domain = urllib.parse.urlparse(hname).hostname
        else:  # user provides custom subdomain
            if hname in ['www']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Forbidden hostname: {hname}."
                    )
            domain = f"{hname}.{base_domain}"
    else:  # we use job_ID as default subdomain
        domain = f"{job_uuid}.{base_domain}"
    return domain


def check_domain(url):
    """
    Check if the domain is free so that we let user deploy to it.

    Parameters:
    * **url**

    Returns None if the checks pass, otherwise raises an Exception.
    """

    # Check if the URL is reachable first
    try:
        r = requests.get(url)
    except requests.exceptions.ConnectionError:
        # URL was not reachable therefore assumed empty
        return None
    except Exception:
        # Other exception happened
        raise HTTPException(
        status_code=401,
        detail=f"We had troubles reaching {url}. Make sure it is a valid domain, "\
                "otherwise contact with support."
        )

    # It still might be available if the error is a 404 coming from Traefik.
    # We still have to check that the error 404 thrown by Traefik and not by some other
    # application. We do this by checking the headers.
    # This is a hacky fix for a limitation in Traefik:
    # https://github.com/traefik/traefik/issues/8141#issuecomment-844548035
    traefik_headers = {'Content-Type', 'X-Content-Type-Options', 'Date', 'Content-Length'}
    headers = set(dict(r.headers).keys())
    xcontent = r.headers.get('X-Content-Type-Options', None)
    lcontent = r.headers.get('Content-Length', None)

    if (headers == traefik_headers) and \
       (xcontent == 'nosniff') and \
       (lcontent == '19'):
        return None

    # In every other case, the URL is already in use.
    raise HTTPException(
        status_code=401,
        detail=f"The domain {url} seems to be taken. Please try again with a new domain or leave the field empty."
        )


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
