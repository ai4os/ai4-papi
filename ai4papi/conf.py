"""
Manage configurations of the API.
"""

from distutils.util import strtobool
import os
from pathlib import Path
from string import Template
import subprocess

import yaml


# Check if we are developing in dev mode or production mode, to disable parts of the
# code that are compute intensive (eg. disables calls to Github API)
IS_PROD = bool(strtobool(i)) if (i := os.getenv("IS_PROD")) else False
IS_DEV = not IS_PROD

# Harbor token is kind of mandatory in production, otherwise snapshots won't work.
HARBOR_USER = "robot$user-snapshots+snapshot-api"
HARBOR_PASS = os.environ.get("HARBOR_ROBOT_PASSWORD")
if not HARBOR_PASS:
    if IS_DEV:
        # Not enforce this for developers
        print(
            'You should define the variable "HARBOR_ROBOT_PASSWORD" to use the "/snapshots" endpoint.'
        )
    else:
        raise Exception('You need to define the variable "HARBOR_ROBOT_PASSWORD".')

# Paths
main_path = Path(__file__).parent.absolute()
paths = {
    "conf": main_path.parent / "etc",
    "media": main_path / "media",
}

# Load main API configuration
with open(paths["conf"] / "main.yaml", "r") as f:
    MAIN_CONF = yaml.safe_load(f)


def load_nomad_job(fpath):
    """
    Load default Nomad job configuration
    """
    with open(fpath, "r") as f:
        raw_job = f.read()
        job_template = Template(raw_job)
    return job_template


def load_yaml_conf(fpath):
    """
    Load user customizable parameters
    """
    with open(fpath, "r") as f:
        conf_full = yaml.safe_load(f)

    conf_values = {}
    for group_name, params in conf_full.items():
        conf_values[group_name] = {}
        for k, v in params.items():
            if "name" not in v.keys():
                raise Exception(f"Parameter {k} needs to have a name.")
            if "value" not in v.keys():
                raise Exception(f"Parameter {k} needs to have a value.")
            conf_values[group_name][k] = v["value"]

    return conf_full, conf_values


# Standard modules
nmd = load_nomad_job(paths["conf"] / "modules" / "nomad.hcl")
yml = load_yaml_conf(paths["conf"] / "modules" / "user.yaml")
MODULES = {
    "nomad": nmd,
    "user": {
        "full": yml[0],
        "values": yml[1],
    },
}

# Tools
tool_dir = paths["conf"] / "tools"
tool_list = [f for f in tool_dir.iterdir() if f.is_dir()]
TOOLS = {}
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
        raise Exception(f"The tool {tool} is missing from the mapping dictionary.")

# OSCAR template
OSCAR = {}
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
