"""
Manage module metadata.
This is the AI4EOSC Exchange API.

Implementation notes:
====================
* We decide to initially implement this in the same repo/API as deployment management (Training API),
but on a different API route.
This is done to avoid duplicating code (eg. authentication) in the initial development phase.
We can always move this to another repo/API in the future if needed.

* Output caching
We are in a transition time, where users still edit their modules metadata in their Github repos.
But reading metadata from Github and returning it isn't fast enough for a seamless experience (~0.3s / module).
This is especially critical for the Overview of the Dashboard where one retrieves metadata of all the modules (N * 0.3s).
We decide to cache the outputs of the functions for up to six hours to enable fast calls in the meantime.
This caching can be eventually removed when we move the metadata to the Exchange database.
Or the caching time be reduced if needed, though 6 hours seems to be a decent compromise, as metadata is not constantly changing.

The tags query parameters are implemented as *tuples*, not lists, as tuples are
immutable objects and lists are not. Only immutable objects can have a hash and
therefore be cached by cachetools.
ref: https://stackoverflow.com/questions/42203673/in-python-why-is-a-tuple-hashable-but-not-a-list
"""

import configparser
import json
from typing import Tuple, Union

from cachetools import cached, TTLCache
from fastapi import APIRouter, HTTPException, Query
import requests
import yaml


router = APIRouter(
    prefix="/modules",
    tags=["modules"],
    responses={404: {"description": "Not found"}},
)


@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_modules_list():
    """
    Retrieve a list of *all* modules.

    This is implemented in a separate function as many functions from this router
    are using this function, so we need to avoid infinite recursions.
    """
    modules_url = "https://raw.githubusercontent.com/deephdc/deep-oc/master/MODULES.yml"
    r = requests.get(modules_url)
    catalog = yaml.safe_load(r.text)
    modules = [i['module'].split('/')[-1] for i in catalog]  # remove github prefix

    return modules


@router.get("/")
@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_filtered_modules_list(
    tags: Union[Tuple, None] = Query(default=None),
    tags_any: Union[Tuple, None] = Query(default=None),
    not_tags: Union[Tuple, None] = Query(default=None),
    not_tags_any: Union[Tuple, None] = Query(default=None),
    ):
    """
    Retrieve a list of all modules, optionally filtering by tags.

    The tag filtering logic is based on [Openstack](https://docs.openstack.org/api-ref/identity/v3/?expanded=list-projects-detail#filtering-and-searching-by-tags):
    * `tags`: Modules that contain all of the specified tags
    * `tags-any`: Modules that contain at least one of the specified tags
    * `not-tags`: Modules that do not contain exactly all of the specified tags
    * `not-tags-any`: Modules that do not contain any one of the specified tags

    You can also use wildcards:
    * `image*` matches all tags _starting_ with "image"
    * `*image` matches all tags _ending_ with "image"
    * `*image*` matches all tags _containing_ the substring "image"
    """
    # Retrieve all modules
    modules = get_modules_list()

    if any([tags, tags_any, not_tags, not_tags_any]):  # apply filtering

        # Move to tag dict for easier manipulation (wildcard substitution)
        td = {
            'tags': tags if tags else [],
            'tags_any': tags_any if tags_any else [],
            'not_tags': not_tags if not_tags else [],
            'not_tags_any': not_tags_any if not_tags_any else [],
        }

        # Replace the wildcards with actual tags
        all_tags = get_modules_tags()
        for k, v in td.items():

            new_tags = []
            for i in v:
                matched_tags = None
                if i.startswith('*') and i.endswith('*'):
                    matched_tags = [j for j in all_tags if (i[1:-1] in j)]
                elif i.startswith('*'):
                    matched_tags = [j for j in all_tags if j.endswith(i[1:])]
                elif i.endswith('*'):
                    matched_tags = [j for j in all_tags if j.startswith(i[:-1])]

                if matched_tags:
                    new_tags += matched_tags
                else:
                    new_tags.append(i)

            td[k] = new_tags

        # Filter modules
        fmodules = []
        for m in modules:
            mtags = set(
                get_module_metadata(m)['keywords']
            )

            conditions = []
            if td['tags']:
                conditions.append(
                    len(mtags.intersection(td['tags'])) == len(td['tags'])
                )
            if td['tags_any']:
                conditions.append(
                    len(mtags.intersection(td['tags_any'])) != 0
                )
            if td['not_tags']:
                conditions.append(
                    len(mtags.intersection(td['not_tags'])) != len(td['not_tags'])
                )
            if td['not_tags_any']:
                conditions.append(
                    len(mtags.intersection(td['not_tags_any'])) == 0
                )

            if all(conditions):
                fmodules.append(m)

        return fmodules

    else:  # no filtering applied
        return modules


@router.get("/summary")
@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_modules_summary():
    """
    Retrieve a list of all modules' basic metadata
    (name, title, summary, keywords).
    """
    summary = []
    keys = ['title', 'summary', 'keywords']
    for m in get_modules_list():
        meta1 = get_module_metadata(m)
        meta = {k: v for k, v in meta1.items() if k in keys}  # filter keys
        meta['name'] = m
        summary.append(meta)
    return summary


@router.get("/tags")
@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_modules_tags():
    """
    Retrieve a list of all the existing tags.
    """
    tags = []
    for m in get_modules_list():
        meta = get_module_metadata(m)
        tags += meta['keywords']
    tags = sorted(set(tags))
    return tags


@router.get("/metadata/{module_name}")
@cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
def get_module_metadata(
    module_name: str,
    ):
    """
    Get the module's full metadata.
    """

    # Check the module is in the modules list
    modules = get_modules_list()
    if module_name not in modules:
        raise HTTPException(
            status_code=400,
            detail="Module {module_name} not in catalog: {modules}",
            )

    # Find what is the default branch of the module
    gitmodules_url = "https://raw.githubusercontent.com/deephdc/deep-oc/master/.gitmodules"
    r = requests.get(gitmodules_url)

    gitmodules_conf = configparser.ConfigParser()
    gitmodules_conf.read_string(r.text)

    section = f'submodule "{module_name}"'
    if "branch" in gitmodules_conf.options(section):
        branch = gitmodules_conf.get(section, "branch")
    else:
        branch = "master"

    # Retrieve metadata from that branch
    metadata_url = f"https://raw.githubusercontent.com/deephdc/{module_name}/{branch}/metadata.json"
    r = requests.get(metadata_url)
    metadata = json.loads(r.text)

    # Format "description" field nicely for the Dashboards Markdown parser
    desc = []
    for l in metadata['description']:
        desc.append(l)

    metadata["description"] = "\n".join(desc)  # single string

    return metadata


@router.put("/metadata/{module_name}")
def update_module_metadata(
    module_name: str,
    ):
    """
    Update the module's metadata.
    TODO: do this when we implement the AI4EOSC Exchange database
    This function needs authentication, users should only be able to edit their own modules
    """
    raise HTTPException(status_code=501)
