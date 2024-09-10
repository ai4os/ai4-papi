"""
Manage catalog stuff.

Both modules and tools share similar workflows so they will inherit from a common
Catalog class. We only finetune methods wheen needed (eg. /config).
Both modules and tools are referred in the common code as "items".

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
import re
from typing import Tuple, Union

from cachetools import cached, TTLCache
from fastapi import HTTPException, Query
import requests

from ai4papi import utils


class Catalog:

    def __init__(self) -> None:
        pass


    @cached(cache=TTLCache(maxsize=1024, ttl=6*60*60))
    def get_items(
        self,
        ):
        """
        Retrieve a dict of *all* items.
        ```
        {'module 1': {
            'url': ***,
            'branch': ***,
            },
            ...
          }
        ```
        This is implemented in a separate function as many functions from this router
        are using this function, so we need to avoid infinite recursions.
        """
        return {}


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
        modules = list(self.get_items().keys())
        # (!): without list(...) FastAPI throws weird error
        # ValueError: [ValueError('dictionary update sequence element #0 has length 1; 2 is required'), TypeError('vars() argument must have __dict__ attribute')]
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
            try:
                meta1 = self.get_metadata(m)
            except Exception:
                # Avoid breaking the whole method if failing to retrieve a module
                print(f'Error retrieving metadata: {m}')
                continue
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
        for m in self.get_items().keys():
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
        # Check if item is in the items list
        items = self.get_items()
        if item_name not in items.keys():
            raise HTTPException(
                status_code=404,
                detail=f"Item {item_name} not in catalog: {list(items.keys())}",
                )

        # Retrieve metadata from default branch
        # Use try/except to avoid that a single module formatting error could take down
        # all the Dashboard
        branch = items[item_name].get("branch", "master")
        url = items[item_name]['url'].replace('github.com', 'raw.githubusercontent.com')
        metadata_url = f"{url}/{branch}/metadata.json"
        try:
            r = requests.get(metadata_url)
            metadata = json.loads(r.text)
        except Exception:
            print(f'Error parsing metadata: {item_name}')
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
                    "dockerfile_repo": f"https://github.com/ai4oshub/{item_name}",
                    "docker_registry_repo": f"ai4os-hub/{item_name}",
                    "code": "",
                }
            }

        # Format "description" field nicely for the Dashboards Markdown parser
        metadata["description"] = "\n".join(metadata["description"])

        # Replace some fields with the info gathered from Github
        pattern = r'github\.com/([^/]+)/([^/]+?)(?:\.git|/)?$'
        match = re.search(pattern, items[item_name]['url'])
        if match:
            owner, repo = match.group(1), match.group(2)
            gh_info = utils.get_github_info(owner, repo)

            metadata['date_creation'] = gh_info.get('created', '')
            # metadata['updated'] = gh_info.get('updated', '')
            metadata['license'] = gh_info.get('license', '')
        else:
            print(f"Failed to parse owner/repo in {items[item_name]['url']}")

        return metadata

    def get_config(
        self,
        ):
        """
        Returns the default configuration (dict) for creating a deployment
        for a specific item. It is prefilled with the appropriate
        docker image and the available docker tags.
        """
        return {}


def retrieve_docker_tags(
    image: str,
    repo: str = 'ai4oshub',
    ):
    """
    Retrieve tags from Dockerhub image
    """
    url = f"https://registry.hub.docker.com/v2/repositories/{repo}/{image}/tags?page_size=100"
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
