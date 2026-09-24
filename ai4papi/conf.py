"""
Manage configurations of the API.
"""

import csv
import os
from pathlib import Path
from string import Template
from typing import Any, TypedDict
import subprocess
import warnings

from dotenv import load_dotenv
import yaml


load_dotenv()

# Check if we are developing in dev mode or production mode, to disable parts of the
# code that are compute intensive (eg. disables calls to Github API)
IS_PROD = os.getenv("IS_PROD", "false").lower() in ("true", "1", "t")
IS_DEV = not IS_PROD


def load_env(varname: str):
    """
    Load environment variables.
    """
    var = os.getenv(varname)

    if not var:
        if IS_DEV:
            # To make the occasional developer's life easier, we raise warnings
            # (instead of errors) if the variable is not defined. To avoid making
            # them define variables needed for sections of code they are not developing.
            warnings.warn(f'"{varname}" envar is not defined')
        else:
            raise RuntimeError(f'You need to define the variable "{varname}".')

    return var


# Harbor credentials
HARBOR_USER = "robot$user-snapshots+snapshot-api"
HARBOR_PASS = load_env("HARBOR_ROBOT_PASSWORD")

# External MCP and LiteLLM services.
LITELLM_URL = load_env("LITELLM_URL")
LITELLM_API_KEY = load_env("LITELLM_API_KEY")
LITELLM_TIMEOUT = float(load_env("LITELLM_TIMEOUT") or 90)
MCP_REGISTRY_URL = load_env("MCP_REGISTRY_URL")
MCP_REGISTRY_TIMEOUT = float(load_env("MCP_REGISTRY_TIMEOUT") or 90)
MCP_NOMAD_DEFAULT_VO = load_env("MCP_NOMAD_DEFAULT_VO") or "vo.ai4eosc.eu"
MCP_NOMAD_STARTUP_TIMEOUT = float(load_env("MCP_NOMAD_STARTUP_TIMEOUT") or 180)
MCP_NOMAD_POLL_INTERVAL = float(load_env("MCP_NOMAD_POLL_INTERVAL") or 2)
MCP_NOMAD_NODE_IMAGE = load_env("MCP_NOMAD_NODE_IMAGE") or "node:22-alpine"
MCP_NOMAD_SUPERGATEWAY_PACKAGE = (
    load_env("MCP_NOMAD_SUPERGATEWAY_PACKAGE") or "supergateway@3.4.3"
)

# Paths
main_path = Path(__file__).parent.absolute()
paths = {
    "conf": main_path.parent / "etc",
    "media": main_path / "media",
}

# Load main API configuration
with open(paths["conf"] / "main.yaml", "r") as f:
    MAIN_CONF = yaml.safe_load(f)


def load_nomad_job(fpath: Path) -> Template:
    """
    Load default Nomad job configuration
    """
    return Template(fpath.read_text(encoding="utf-8"))


def load_yaml_conf(fpath: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Load user customizable parameters
    """
    with open(fpath, "r") as f:
        conf_full: dict[str, Any] = yaml.safe_load(f)

    conf_values = {}
    for group_name, params in conf_full.items():
        conf_values[group_name] = {}
        for k, v in params.items():
            for i in ["name", "value"]:
                if i not in v:
                    raise ValueError(f"Parameter {k} needs to have an {i}.")
            conf_values[group_name][k] = v["value"]

    return conf_full, conf_values


# Standard modules
class AssetConfig(TypedDict):
    nomad: Template
    user: dict[str, Any]


nmd = load_nomad_job(paths["conf"] / "modules" / "nomad.hcl")
yml = load_yaml_conf(paths["conf"] / "modules" / "user.yaml")
MODULES: AssetConfig = {
    "nomad": nmd,
    "user": {
        "full": yml[0],
        "values": yml[1],
    },
}

# Tools
tool_dir = paths["conf"] / "tools"
tool_list = [f for f in tool_dir.iterdir() if f.is_dir()]
TOOLS: dict[str, AssetConfig] = {}
for tool_path in tool_list:
    nmd = load_nomad_job(tool_path / "nomad.hcl")
    yml = load_yaml_conf(tool_path / "user.yaml")
    TOOLS[tool_path.name] = {
        "nomad": nmd,
        "user": {
            "full": yml[0],
            "values": yml[1],
        },
    }

# For tools, map the Nomad job name prefixes to tool IDs
tools_nomad2id = {
    "fl": "ai4os-federated-server",
    "cvat": "ai4os-cvat",
    "nvflare": "ai4os-nvflare",
    "llm": "ai4os-llm",
    "ai4life": "ai4os-ai4life-loader",
    "devenv": "ai4os-dev-env",
}
for tool in TOOLS.keys():
    if tool not in tools_nomad2id.values():
        raise KeyError(f"The tool {tool} is missing from the mapping dictionary.")

# OSCAR template

OSCAR: dict[str, Any] = {}
with open(paths["conf"] / "oscar" / "service.yaml", "r") as f:
    OSCAR["service"] = Template(f.read())
yml = load_yaml_conf(paths["conf"] / "oscar" / "user.yaml")
OSCAR["user"] = {
    "full": yml[0],
    "values": yml[1],
}

# vLLM conf
with open(paths["conf"] / "vllm.yaml", "r") as f:
    VLLM = yaml.safe_load(f)

# Try-me endpoints
nmd = load_nomad_job(paths["conf"] / "try_me" / "nomad.hcl")
TRY_ME = {
    "nomad": nmd,
}

# Snapshot endpoints
nmd = load_nomad_job(paths["conf"] / "snapshots" / "nomad.hcl")
SNAPSHOTS = {
    "nomad": nmd,
}

# Self-deployed MCP servers
nmd = load_nomad_job(paths["conf"] / "mcp" / "nomad.hcl")
yml = load_yaml_conf(paths["conf"] / "mcp" / "user.yaml")
MCP = {
    "nomad": nmd,
    "user": {
        "full": yml[0],
        "values": yml[1],
    },
    "register": (paths["conf"] / "mcp" / "register.py").read_text(encoding="utf-8"),
    "deregister": (paths["conf"] / "mcp" / "deregister.py").read_text(encoding="utf-8"),
}

# Load datacenter info file
pth = main_path.parent / "var" / "datacenters.csv"
datacenters: dict[str, dict] = {}
with open(pth, "r") as f:
    reader = csv.DictReader(f, delimiter=",")
    if not reader.fieldnames:
        raise ValueError("CSV is missing fieldnames")
    dc_keys = list(reader.fieldnames)
    dc_keys.remove("name")
    for row in reader:
        for k, v in row.items():
            if k == "name":
                name = str(v)
                dc_val: dict[str, str | float | dict] = dict.fromkeys(dc_keys, 0)
                dc_val["nodes"] = {}
                datacenters[name] = dc_val
            elif k == "country":
                datacenters[name][k] = v
            else:
                datacenters[name][k] = float(v)

# Retrieve git info from PAPI, to show current version in the docs
papi_commit = subprocess.run(
    ["git", "log", "-1", "--format=%H"],
    stdout=subprocess.PIPE,
    text=True,
    cwd=main_path,
).stdout.strip()
papi_branch = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    stdout=subprocess.PIPE,
    text=True,
    cwd=main_path,
).stdout.strip()
papi_branch = papi_branch.split("/")[-1]  # remove the "origin/" part
