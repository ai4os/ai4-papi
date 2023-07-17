"""
Manage catalog stuff.

Both modules and tools share similar workflows so they will inherit from a common
Catalog class. We only finetune methods wheen needed (eg. /config).

Implementation notes:
=====================
* Output caching
We are in a transition time, where users still edit their modules metadata in their Github repos.
But reading metadata from Github and returning it isn't fast enough for a seamless experience (~0.3s / module).
This is especially critical for the Overview of the Dashboard where one retrieves metadata of all the modules (N * 0.3s).
We decide to cache the outputs of the functions for up to six hours to enable fast calls in the meantime.

The tags query parameters are implemented as *tuples*, not lists, as tuples are
immutable objects and lists are not. Only immutable objects can have a hash and
therefore be cached by cachetools.
ref: https://stackoverflow.com/questions/42203673/in-python-why-is-a-tuple-hashable-but-not-a-list

* Somes names need to be reserved to avoid clashes between URL paths.
This means you cannot name your modules like those names (eg. tags, detail, etc)
"""

import configparser
import json
from typing import Tuple, Union

from cachetools import cached, TTLCache
from fastapi import HTTPException, Query
import re
import requests


class Catalog:

    def __init__(self) -> None:
        pass


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_list(
        self,
        ):
        """
        Retrieve a list of *all* items.

        This is implemented in a separate function as many functions from this router
        are using this function, so we need to avoid infinite recursions.
        """
        return []


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_filtered_list(
        self,
        tags: Union[Tuple, None] = Query(default=None),
        tags_any: Union[Tuple, None] = Query(default=None),
        not_tags: Union[Tuple, None] = Query(default=None),
        not_tags_any: Union[Tuple, None] = Query(default=None),
        ):
        """
        Retrieve a list of all items, optionally filtering by tags.

        The tag filtering logic is based on [Openstack](https://docs.openstack.org/api-ref/identity/v3/?expanded=list-projects-detail#filtering-and-searching-by-tags):
        * `tags`: Items that contain all of the specified tags
        * `tags-any`: Items that contain at least one of the specified tags
        * `not-tags`: Items that do not contain exactly all of the specified tags
        * `not-tags-any`: Items that do not contain any one of the specified tags

        You can also use wildcards:
        * `image*` matches all tags _starting_ with "image"
        * `*image` matches all tags _ending_ with "image"
        * `*image*` matches all tags _containing_ the substring "image"


        """
        # Retrieve all modules
        modules = self.get_list()

        if any([tags, tags_any, not_tags, not_tags_any]):  # apply filtering

            # Move to tag dict for easier manipulation (wildcard substitution)
            td = {
                'tags': tags if tags else [],
                'tags_any': tags_any if tags_any else [],
                'not_tags': not_tags if not_tags else [],
                'not_tags_any': not_tags_any if not_tags_any else [],
            }

            # Replace the wildcards with actual tags
            all_tags = self.get_tags()
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
                    self.get_metadata(m)['keywords']
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


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_summary(
        self,
        tags: Union[Tuple, None] = Query(default=None),
        tags_any: Union[Tuple, None] = Query(default=None),
        not_tags: Union[Tuple, None] = Query(default=None),
        not_tags_any: Union[Tuple, None] = Query(default=None),
        ):
        """
        Retrieve a list of all items' basic metadata
        (name, title, summary, keywords), optionally filtering items by tags.

        The tag filtering logic is based on [Openstack](https://docs.openstack.org/api-ref/identity/v3/?expanded=list-projects-detail#filtering-and-searching-by-tags):
        * `tags`: Items that contain all of the specified tags
        * `tags-any`: Items that contain at least one of the specified tags
        * `not-tags`: Items that do not contain exactly all of the specified tags
        * `not-tags-any`: Items that do not contain any one of the specified tags

        You can also use wildcards:
        * `image*` matches all tags _starting_ with "image"
        * `*image` matches all tags _ending_ with "image"
        * `*image*` matches all tags _containing_ the substring "image"
        """
        summary = []
        keys = ['title', 'summary', 'keywords']
        modules = self.get_filtered_list(
            tags=tags,
            tags_any=tags_any,
            not_tags=not_tags,
            not_tags_any=not_tags_any,
            )
        for m in modules:
            meta1 = self.get_metadata(m)
            meta = {k: v for k, v in meta1.items() if k in keys}  # filter keys
            meta['name'] = m
            summary.append(meta)
        return summary


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_tags(
        self,
        ):
        """
        Retrieve a list of all the existing tags.
        """
        tags = []
        for m in self.get_list():
            meta = self.get_metadata(m)
            tags += meta['keywords']
        tags = sorted(set(tags))
        return tags


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_metadata(
        self,
        item_name: str,
        ):
        """
        Get the item's full metadata.
        """

        # Check the module is in the modules list
        items = self.get_list()
        if item_name not in items:
            raise HTTPException(
                status_code=400,
                detail="Item {item_name} not in catalog: {items}",
                )

        # Read the index of modules from Github
        gitmodules_url = "https://raw.githubusercontent.com/deephdc/deep-oc/master/.gitmodules"
        r = requests.get(gitmodules_url)

        cfg = configparser.ConfigParser()
        cfg.read_string(r.text)

        # Convert ConfigParser to cleaner dict
        # and retrieve default branch (if no branch use master)
        modules_conf = {
            re.search(r'submodule "(.*)"', s).group(1).lower():
            # 'submodule "DEEP-OC-..."' --> 'deep-oc-...'
                dict(cfg.items(s))
                for s in cfg.sections()
        }
        branch = modules_conf[item_name].get("branch", "master")

        # Retrieve metadata from that branch
        # Use try/except to avoid that a single module formatting error could take down
        # all the Dashboard
        metadata_url = f"https://raw.githubusercontent.com/deephdc/{item_name}/{branch}/metadata.json"

        try:
            r = requests.get(metadata_url)
            metadata = json.loads(r.text)

        except Exception:
            metadata = {
                "title": item_name,
                "summary": "",
                "description": [
                    "The metadata of this module could not be retrieved probably due to a ",
                    "JSON formatting error from the module maintainer."
                ],
                "keywords": [],
                "license": "",
                "date_creation": "",
                "sources": {
                    "dockerfile_repo": f"https://github.com/deephdc/{item_name}",
                    "docker_registry_repo": f"deephdc/{item_name}",
                    "code": "",
                }
            }

        # Format "description" field nicely for the Dashboards Markdown parser
        metadata["description"] = "\n".join(metadata["description"])

        return metadata

    def get_config(
        self,
    ):
        return {}


def retrieve_docker_tags(
    image: str,
    repo: str = 'deephdc',
):
    """
    Retrieve tags from Dockerhub image
    """
    url = f"https://registry.hub.docker.com/v2/repositories/{repo}/{image}/tags"
    try:
        r = requests.get(url)
        r.raise_for_status()
        r = r.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"Could not retrieve Docker tags from {repo}/{image}.",
            )
    tags = [i["name"] for i in r["results"]]
    return tags
