"""
Return stats of the modules
"""

import copy

from cachetools import cached, TTLCache
from fastapi import APIRouter
from fastapi.security import HTTPBearer

from datetime import date

import requests
from collections import defaultdict

from ai4papi.routers.v1.catalog.common import Catalog

router = APIRouter(
    prefix="/modules",
    tags=["Modules stats"],
    responses={404: {"description": "Not found"}},
)
security = HTTPBearer()

modules_stats = None


@router.get("")
@cached(cache=TTLCache(maxsize=1024, ttl=30))
def get_modules_stats():
    """
    Returns the following stats of the catalog modules: 
        * Number of trymes (Plausible)
        * Number of module views (Plausible)
        * GitHub stars
        * Dockerhub downloads
        * Number of nomad deployments (TODO)
        * Number of oscar services, number of oscar calls (TODO)
    """

    # global modules_stats
    # if not modules_stats:
    #     # If PAPI is used as a package, cluster_stats will be None, as the background
    #     # computation of `get_modules_stats_bg()` is only started when PAPI is launched
    #     # with uvicorn.
    #     # So if None, we need to initialize it
    #     modules_stats = get_modules_stats_bg()
    # stats = copy.deepcopy(modules_stats)


    modules_stats = get_modules_stats_bg()
    stats = copy.deepcopy(modules_stats)

    return stats


#@cached(cache=TTLCache(maxsize=1024, ttl=86400))
def get_modules_stats_bg():
    """
    Background task that computes the stats of the modules.
    The TTL of this task should be >= than the repeat frequency of the thread defined
    in main.py.
    """

    start_date = "2020-01-01"
    end_date = date.today().isoformat()

    modules = Catalog(
        repo="ai4os-hub/modules-catalog",
        item_type="module",
    )

    items = modules.get_items()
    module_ids = list(items.keys())
        
    # Number of trymes
    tryme_stats = get_plausible_pageviews(start_date, end_date, "/modules/<module-id>/try-me-nomad", module_ids)
    
    # Number of module detail views
    detail_stats = get_plausible_pageviews(start_date, end_date, "/modules/<module-id>", module_ids)
    
    # TODO: number of Nomad deployments
    
    # TODO: Numer of OSCAR services/calls

    # Group all the metrics 
    stats = []
    for module_id in module_ids:
        detail = modules._get_metadata(module_id)

        stats_obj = {
            "module_id": module_id,
            "number_of_trymes": tryme_stats.get(module_id, 0),
            "number_of_module_views": detail_stats.get(module_id, 0),
            "github_stars": get_github_stars(detail['links']['source_code']),      
            "dockerhub_downloads": get_dockerhub_downloads(detail['links']['docker_image'])
        }
        stats.append(stats_obj)

    # Set the new shared variable
    global modules_stats
    modules_stats = stats
    return modules_stats


######################################
########### Plausible stats ##########
######################################
API_KEY = ''  # TODO: add this as an env variable
SITE_IDS = [
    'dashboard.cloud.ai4eosc.eu',
    'dashboard.cloud.imagine-ai.eu',
    'ai4life.cloud.ai4eosc.eu'
]
BASE_URL = 'https://stats.services.ai4os.eu/api/v1/stats/breakdown'
HEADERS = {'Authorization': f'Bearer {API_KEY}'}

def fetch_paginated_results(base_params):
    results = []
    page = 1
    while True:
        params = base_params.copy()
        params['limit'] = 100
        params['page'] = page
        response = requests.get(BASE_URL, headers=HEADERS, params=params)
        if response.status_code != 200:
            print(f"Error fetching page {page}: {response.status_code} {response.text}")
            break
        page_results = response.json().get('results', [])
        if not page_results:
            break
        results.extend(page_results)
        page += 1
    return results

def normalize_path(path):
    parts = path.strip("/").split("/")
    index = parts.index("modules")
    return parts[index + 1]

def get_plausible_pageviews(start_date: str, end_date: str, page_filter_template: str, module_ids: list[str]) -> dict:
    date_range = f"{start_date},{end_date}"
    module_views = defaultdict(int)

    for site_id in SITE_IDS:
        print(f"Fetching pages for {site_id}")
        base_params = {
            'site_id': site_id,
            'period': 'custom',
            'date': date_range,
            'property': 'event:page',
        }

        all_pages = fetch_paginated_results(base_params)
        all_paths = [page['page'] for page in all_pages]

        for module_id in module_ids:
            path_expected = page_filter_template.replace("<module-id>", module_id)

            for actual_path in all_paths:
                if actual_path.strip("/").endswith(path_expected):
                    params = {
                        'site_id': site_id,
                        'period': 'custom',
                        'date': date_range,
                        'property': 'event:page',
                        'metrics': 'pageviews',
                        'filters': f'event:page=={actual_path}',
                    }

                    country_results = fetch_paginated_results(params)
                    for result in country_results:
                        pageviews = result.get('pageviews', 0)
                        module_views[module_id] += pageviews

    return dict(module_views)


######################################
########### Github stars #############
######################################
def get_github_stars(id: str) -> int:    
    id_parts = id.split('/')
    url = f"https://api.github.com/repos/{id_parts[-2]}/{id_parts[-1]}"
    headers = {
        "Accept": "application/vnd.github.v3+json"
    }
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        stars = data.get("stargazers_count", 0)
    else:
        print(f"Error getting stars for ai4os-hub/{id_parts[-2]}/{id_parts[-1]}: {response.status_code}")
        stars = 0

    return stars


######################################
######## Dockerhub downloads #########
######################################
def get_dockerhub_downloads(id: str) -> int:
    url = f"https://hub.docker.com/v2/repositories/{id}"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        downloads = data.get("pull_count", 0)
    else:
        print(f"Error fetching DockerHub data for {id}: {response.status_code}")
        downloads = 0

    return downloads