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
import os
import re
from typing import Tuple, Union
import yaml

import ai4_metadata.validate
from cachetools import cached, TTLCache
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPBearer
import requests

from ai4papi import utils
import ai4papi.conf as papiconf


security = HTTPBearer()

# Jenkins token is mandatory in production
JENKINS_TOKEN = os.getenv("PAPI_JENKINS_TOKEN")
if not JENKINS_TOKEN:
    if papiconf.IS_DEV:  # Not enforced for developers
        print('"JENKINS_TOKEN" envar is not defined')
    else:
        raise Exception('You need to define the variable "JENKINS_TOKEN".')


class Catalog:
    def __init__(self, repo: str, item_type: str = "item") -> None:
        """
        Parameters:
        * repo: Github repo where the catalog is hosted (via git submodules)
        * item_type: Name to display in messages (eg. "module", "tool")
        """
        self.repo = repo
        self.item_type = item_type

    @cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
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
        gitmodules_url = (
            f"https://raw.githubusercontent.com/{self.repo}/master/.gitmodules"
        )
        r = requests.get(gitmodules_url)

        cfg = configparser.ConfigParser()
        cfg.read_string(r.text)

        modules = {}
        for section in cfg.sections():
            items = dict(cfg.items(section))
            key = items.pop("path")
            items["url"] = items["url"].replace(".git", "")  # remove `.git`, if present
            modules[key] = items

        # In the case of the tools repo, make sure to remove any tool that is not yet
        # supported by PAPI (use the ^ operator to only keep common items)
        if "tool" in self.repo:
            for tool_name in papiconf.TOOLS.keys() ^ modules.keys():
                _ = modules.pop(tool_name)

        return modules

    @cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
    def get_filtered_list(
        self,
        tags: Union[Tuple, None] = Query(default=None),
        tags_any: Union[Tuple, None] = Query(default=None),
        not_tags: Union[Tuple, None] = Query(default=None),
        not_tags_any: Union[Tuple, None] = Query(default=None),
    ):
        """
        Retrieve a list of all items.

        Tag-related fields are kept to avoid breaking backward-compatibility but
        aren't actually serving any purpose.
        """
        # Retrieve all modules
        modules = list(self.get_items().keys())
        # (!): without list(...) FastAPI throws weird error
        # ValueError: [ValueError('dictionary update sequence element #0 has length 1; 2 is required'), TypeError('vars() argument must have __dict__ attribute')]
        return modules

    @cached(cache=TTLCache(maxsize=1024, ttl=6 * 60 * 60))
    def get_summary(
        self,
        tags: Union[Tuple, None] = Query(default=None),
        tags_any: Union[Tuple, None] = Query(default=None),
        not_tags: Union[Tuple, None] = Query(default=None),
        not_tags_any: Union[Tuple, None] = Query(default=None),
    ):
        """
        Retrieve a list of all items' basic metadata.

        Tag-related fields are kept to avoid breaking backward-compatibility but
        aren't actually serving any purpose.
        """
        modules = self.get_filtered_list()
        summary = []
        ignore = ["description", "links"]  # don't send this info to decrease latency
        for m in modules:
            try:
                meta1 = self.get_metadata(m)
            except Exception:
                # Avoid breaking the whole method if failing to retrieve a module
                print(f"Error retrieving metadata: {m}")
                continue
            meta = {k: v for k, v in meta1.items() if k not in ignore}  # filter keys
            meta["name"] = m
            summary.append(meta)
        return summary

    def get_tags(
        self,
    ):
        """
        Retrieve a list of all the existing tags.
        Now deprecated, kept to avoid breaking backward-compatibility.
        Returns an empty list.
        """
        return []

    def get_metadata(
        self,
        item_name: str,
    ):
        """
        Get the item's full metadata.
        """
        return self._get_metadata(item_name=item_name, force=False)

    @cached(
        cache=TTLCache(maxsize=1024, ttl=7 * 24 * 60 * 60),
        key=lambda self, item_name, **kw: item_name,
    )
    def _get_metadata(
        self,
        item_name: str,
        force=False,
    ):
        """
        Get the item's full metadata.

        The function is *internal* because we don't want to expose the "force" parameter
        to users.

        We cache for 1 week because the cache is manually expired by Jenkins and refreshed
        each time a module is updated (catalog refresh method).
        This 1 week expiration is just a backup in case Jenkins does not work.
        """
        print(f"Retrieving metadata from {item_name}")

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
        url = items[item_name]["url"].replace("github.com", "raw.githubusercontent.com")
        metadata_url = f"{url}/{branch}/ai4-metadata.yml"

        error = None
        # Try to retrieve the metadata from Github
        r = requests.get(metadata_url)
        if not r.ok:
            error = (
                "The metadata of this module could not be retrieved because the "
                "module is lacking a metadata file (`ai4-metadata.yml`)."
            )
        else:
            # Try to load the YML file
            try:
                metadata = yaml.safe_load(r.text)
            except Exception:
                metadata = None
                error = (
                    "The metadata of this module could not be retrieved because the "
                    "metadata file is badly formatted (`ai4-metadata.yml`)."
                )

            # Since we are loading the metadata directly from the repo main branch,
            # we cannot know if they have successfully passed or not the Jenkins
            # validation. So we have to validate them, just in case we have naughty users.
            if metadata:
                try:
                    version = ai4_metadata.get_latest_version()
                    schema = ai4_metadata.get_schema(version)
                    ai4_metadata.validate.validate(instance=metadata, schema=schema)
                except Exception:
                    error = (
                        "The metadata of this module has failed to comply with the "
                        "specifications of the AI4EOSC Platform (see the "
                        "[metadata validator](https://github.com/ai4os/ai4-metadata))."
                    )

                # Make sure the repo belongs to one of supported orgs
                pattern = r"https?:\/\/(www\.)?github\.com\/([^\/]+)\/"
                match = re.search(pattern, metadata["links"]["source_code"])
                github_org = match.group(2) if match else None
                if not github_org:
                    error = (
                        "This module does not seem to have a valid Github source code. "
                        "If you are the developer of this module, please check the "
                        '"source_code" link in your metadata.'
                    )
                if github_org not in ["ai4os", "ai4os-hub", "deephdc"]:
                    error = (
                        "This module belongs to a Github organization not supported by "
                        "the project. If you are the developer of this module, please "
                        'check the "source_code" link in your metadata.'
                    )

        # If any of the previous steps raised an error, load a metadata placeholder
        if error:
            print(f"  [Error] {error}")
            metadata = {
                "id": item_name,
                "metadata_version": "2.0.0",
                "title": item_name,
                "summary": "",
                "description": error,
                "doi": "",
                "dates": {
                    "created": "",
                    "updated": "",
                },
                "links": {
                    "documentation": "",
                    "source_code": "",
                    "docker_image": "",
                    "ai4_template": "",
                    "dataset": "",
                    "weights": "",
                    "citation": "",
                    "base_model": "",
                },
                "tags": ["invalid metadata"],
                "tasks": [],
                "categories": [],
                "libraries": [],
                "data-type": [],
            }

        else:
            # Replace some fields with the info gathered from Github
            pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git|/)?$"
            match = re.search(pattern, items[item_name]["url"])
            if match:
                owner, repo = match.group(1), match.group(2)
                if force:
                    utils.get_github_info.cache.pop((owner, repo), None)
                gh_info = utils.get_github_info(owner, repo)

                metadata.setdefault("dates", {})
                metadata["dates"]["created"] = gh_info.get("created", "")
                metadata["dates"]["updated"] = gh_info.get("updated", "")
                metadata["license"] = gh_info.get("license", "")
                metadata["links"]["source_code"] = f"https://github.com/{owner}/{repo}"

            # Add Jenkins CI/CD links
            metadata["links"]["cicd_url"] = (
                f"https://jenkins.services.ai4os.eu/job/{github_org}/job/{item_name}/job/{branch}/"
            )
            metadata["links"]["cicd_badge"] = (
                f"https://jenkins.services.ai4os.eu/buildStatus/icon?job={github_org}/{item_name}/{branch}"
            )

            # Add DockerHub
            # TODO: when the migration is finished, we have to generate the url from the module name
            # (ie. ignore the value coming from the metadata)
            metadata["links"]["docker_image"] = metadata["links"]["docker_image"].strip(
                "/ "
            )

            # Add the item name
            metadata["id"] = item_name

        return metadata

    def refresh_catalog(
        self,
        item_name: str = None,
        authorization=Depends(security),
    ):
        """
        Refresh PAPI catalog.
        If a particular item is provided, expire the metadata cache of a given item
        and recompute new cache value.
        """
        # Check if token is valid
        if authorization.credentials != JENKINS_TOKEN:
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization token.",
            )

        # First refresh the items in the catalog, because this item might be a
        # new addition to the catalog (ie. not present since last parsing the catalog)
        self.get_items.cache_clear()
        self.get_items()

        # If no item name, then we refresh only the catalog index
        if not item_name:
            return {"message": "Catalog refreshed successfully"}

        # Check if the item is indeed valid
        if item_name not in self.get_items().keys():
            raise HTTPException(
                status_code=400,
                detail=f"{item_name} is not an available {self.item_type}.",
            )

        # Refresh metadata
        # We use "force=True" to also refresh Github info
        try:
            self._get_metadata.cache.pop(item_name, None)
            self._get_metadata(item_name, force=True)
            return {"message": "Cache refreshed successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

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
    repo: str = "ai4oshub",
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
