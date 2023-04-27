"""API routes that manage module metadata."""

# Implementation notes:
# ====================
# * We decide to initially implement this in the same repo/API as deployment management
# (Training API), but on a different API route.  This is done to avoid duplicating code
# (eg. authentication) in the initial development phase.  We can always move this to
# another repo/API in the future if needed.
#
# * Output caching
# We are in a transition time, where users still edit their modules metadata in their
# Github repos.  But reading metadata from Github and returning it isn't fast enough for
# a seamless experience (~0.3s / module).  This is especially critical for the Overview
# of the Dashboard where one retrieves metadata of all the modules (N * 0.3s).  We
# decide to cache the outputs of the functions for up to six hours to enable fast calls
# in the meantime.  This caching can be eventually removed when we move the metadata to
# the Exchange database.  Or the caching time be reduced if needed, though 6 hours seems
# to be a decent compromise, as metadata is not constantly changing.

import configparser
import json

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException
import requests
import yaml


router = APIRouter(
    prefix="/modules",
    tags=["modules"],
    responses={404: {"description": "Not found"}},
)

base_org_url = "https://raw.githubusercontent.com/deephdc/"


@router.get("/")
@cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
def get_modules_list():
    """Retrieve a list of all modules."""
    modules_url = f"{base_org_url}/deep-oc/master/MODULES.yml"
    r = requests.get(modules_url, timeout=10)
    catalog = yaml.safe_load(r.text)
    modules = [i["module"].split("/")[-1] for i in catalog]  # remove github prefix
    return modules


@router.get("/summary")
@cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
def get_modules_summary():
    """Retrieve a list of modules with basic metadata.

    This will return a list of (name, title, summary, keywords).
    """
    summary = []
    keys = ["title", "summary", "keywords"]
    for m in get_modules_list():
        meta1 = get_module_metadata(m)
        meta = {k: v for k, v in meta1.items() if k in keys}  # filter keys
        meta["name"] = m
        summary.append(meta)
    return summary


@router.get("/metadata/{module_name}")
@cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
def get_module_metadata(module_name: str):
    """Get the module's full metadata."""
    # Check the module is in the modules list
    modules = get_modules_list()
    if module_name not in modules:
        raise HTTPException(
            status_code=400,
            detail="Module {module_name} not in catalog: {modules}",
        )

    # Find what is the default branch of the module
    gitmodules_url = f"{base_org_url}/deep-oc/master/.gitmodules"
    r = requests.get(gitmodules_url, timeout=10)

    gitmodules_conf = configparser.ConfigParser()
    gitmodules_conf.read_string(r.text)

    section = f'submodule "{module_name}"'
    if "branch" in gitmodules_conf.options(section):
        branch = gitmodules_conf.get(section, "branch")
    else:
        branch = "master"

    # Retrieve metadata from that branch
    metadata_url = f"{base_org_url}/{module_name}/{branch}/metadata.json"
    r = requests.get(metadata_url, timeout=10)
    metadata = json.loads(r.text)

    # Format "description" field nicely for the Dashboards Markdown parser
    desc = []
    for aux in metadata["description"]:
        desc.append(aux)

    metadata["description"] = "\n".join(desc)  # single string

    return metadata


@router.put("/metadata/{module_name}")
def update_module_metadata(module_name: str):
    """Update the module's metadata."""
    # TODO: do this when we implement the AI4EOSC Exchange database
    # This function needs authentication, users should only be able to edit their own
    # modules
    raise HTTPException(status_code=501)
