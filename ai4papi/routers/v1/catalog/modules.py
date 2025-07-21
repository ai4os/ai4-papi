from copy import deepcopy
import types

from fastapi import APIRouter, HTTPException

from ai4papi import quotas, nomad, utils
import ai4papi.conf as papiconf
from .common import Catalog, retrieve_docker_tags


gpu_specs = utils.gpu_specs()


def get_config(
    self,
    item_name: str,
    vo: str,
):
    # Check if module exists
    modules = self.get_items()
    if item_name not in modules.keys():
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available module.",
        )

    # Retrieve module configuration
    conf = deepcopy(papiconf.MODULES["user"]["full"])

    # Retrieve module metadata
    metadata = self.get_metadata(item_name)

    # Parse docker registry
    registry = metadata["links"]["docker_image"]
    repo, image = registry.split("/")[-2:]
    if repo not in ["deephdc", "ai4oshub"]:
        repo = "ai4oshub"

    # Fill with correct Docker image
    conf["general"]["docker_image"]["value"] = f"{repo}/{image}"

    # Add available Docker tags
    tags = retrieve_docker_tags(image=image, repo=repo)
    conf["general"]["docker_tag"]["options"] = tags
    conf["general"]["docker_tag"]["value"] = tags[0]

    # Modify the resources limits for a given user or VO
    conf["hardware"] = quotas.limit_resources(
        item_name=item_name,
        vo=vo,
    )

    # Modify default hardware value with user preferences, as long as they are within
    # the allowed limits
    meta_inference = metadata.get("resources", {}).get("inference", {})  # user request
    final = {}  # final deployment values
    mismatches = {}
    meta2conf = {
        "cpu": "cpu_num",
        "memory_MB": "ram",
    }
    for k, v in meta2conf.items():
        final[k] = meta_inference.get(k, conf["hardware"][v]["value"])
        final[k] = max(final[k], conf["hardware"][v]["range"][0])
        final[k] = min(final[k], conf["hardware"][v]["range"][1])
        conf["hardware"][v]["value"] = final[k]
        if (user_k := meta_inference.get(k)) and user_k > final[k]:
            mismatches[k] = f"Requested: {user_k}, Max allowed: {final[k]}"

    # Fill with available GPU models in the cluster
    # Additionally filter out models that do not meet user requirements
    nomad_models = nomad.common.get_gpu_models(vo)
    models = []
    for m in nomad_models:
        if m not in gpu_specs.keys():
            print(f"Nomad model not found in PAPI GPU specs table: {m}")
            continue
        for k in ["gpu_memory_MB", "gpu_compute_capability"]:
            if (r := meta_inference.get(k)) and r > gpu_specs[m][k]:
                break
        else:
            models.append(m)
    if not models:
        # If no GPU models meet the requirements let the user use any model
        conf["hardware"]["gpu_type"]["options"] += nomad_models
        gpu_mismatch = []
        for k in ["gpu_memory_MB", "gpu_compute_capability"]:
            if r := meta_inference.get(k):
                gpu_mismatch.append(f"<li><strong>{k}</strong>: {r}</li>")
        gpu_mismatch = f"<ul>{' '.join(gpu_mismatch)}</ul>"
        mismatches["gpu"] = (
            "No GPU model could fullfil all requirements at once."
            "<ul>"
            f"<li> Requested: {gpu_mismatch} </li> "
            f'<li> <a href="https://github.com/ai4os/ai4-papi/blob/master/var/gpu_models.csv"> Available models</a>: {", ".join(nomad_models)} </li> '
            "</ul>"
        )
    else:
        conf["hardware"]["gpu_type"]["options"] += models

    # Show warning if we couldn't accommodate user requirements
    if mismatches:
        warning = (
            "The developer of the module specified a recommended amount of resources "
            "that could not be met in Nomad deployments. "
            "Therefore, you might experience some issues when using this module for "
            "inference. The following resources could not be met: <ul>"
        )
        for k, v in mismatches.items():
            warning += f"<li> <strong>{k}</strong>: {v} </li>"
        conf["hardware"]["warning"] = warning + "</ul>"

    return conf


Modules = Catalog(
    repo="ai4os-hub/modules-catalog",
    item_type="module",
)
Modules.get_config = types.MethodType(get_config, Modules)


router = APIRouter(
    prefix="/modules",
    tags=["Modules catalog"],
    responses={404: {"description": "Not found"}},
)
router.add_api_route(
    "",
    Modules.get_filtered_list,
    methods=["GET"],
)
router.add_api_route(
    "/detail",
    Modules.get_summary,
    methods=["GET"],
)
router.add_api_route(
    "/tags",
    Modules.get_tags,
    methods=["GET"],
    deprecated=True,
)
router.add_api_route(
    "/{item_name}/metadata",
    Modules.get_metadata,
    methods=["GET"],
)
router.add_api_route(
    "/{item_name}/config",
    Modules.get_config,
    methods=["GET"],
)

router.add_api_route(
    "/refresh",
    Modules.refresh_catalog,
    methods=["PUT"],
)
