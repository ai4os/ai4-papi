"""
Manage configurations of the API.
"""

from pathlib import Path
from string import Template
import subprocess

import yaml


# Paths
main_path = Path(__file__).parent.absolute()
paths = {
    "conf": main_path.parent / "etc",
    "media": main_path / "media",
}

# Load main API configuration
with open(paths['conf'] / 'main.yaml', 'r') as f:
    MAIN_CONF = yaml.safe_load(f)


def load_nomad_job(fpath):
    """
    Load default Nomad job configuration
    """
    with open(fpath, 'r') as f:
        raw_job = f.read()
        job_template = Template(raw_job)
    return job_template


def load_yaml_conf(fpath):
    """
    Load user customizable parameters
    """
    with open(fpath, 'r') as f:
        conf_full = yaml.safe_load(f)

    conf_values = {}
    for group_name, params in conf_full.items():
        conf_values[group_name] = {}
        for k, v in params.items():
            if 'name' not in v.keys():
                raise Exception(f"Parameter {k} needs to have a name.")
            if 'value' not in v.keys():
                raise Exception(f"Parameter {k} needs to have a value.")
            conf_values[group_name][k] = v['value']

    return conf_full, conf_values


# Standard modules
nmd = load_nomad_job(paths['conf'] / 'modules' / 'nomad.hcl')
yml = load_yaml_conf(paths['conf'] / 'modules' / 'user.yaml')
MODULES = {
    'nomad': nmd,
    'user': {
        'full': yml[0],
        'values': yml[1],
    },
}

# Tools
tool_dir = paths['conf'] / 'tools'
tool_list = [f for f in tool_dir.iterdir() if f.is_dir()]
TOOLS = {}
for tool_path in tool_list:
    nmd = load_nomad_job(tool_path / 'nomad.hcl')
    yml = load_yaml_conf(tool_path / 'user.yaml')
    TOOLS[tool_path.name] = {
        'nomad': nmd,
        'user': {
            'full': yml[0],
            'values': yml[1],
        },
    }

# OSCAR template
with open(paths['conf'] / 'oscar.yaml', 'r') as f:
    OSCAR_TMPL = Template(f.read())

# Try-me endpoints
nmd = load_nomad_job(paths['conf'] / 'try_me' / 'nomad.hcl')
TRY_ME = {
    'nomad': nmd,
}

# Snapshot endpoints
nmd = load_nomad_job(paths['conf'] / 'snapshots' / 'nomad.hcl')
SNAPSHOTS = {
    'nomad': nmd,
}

# Retrieve git info from PAPI, to show current version in the docs
papi_commit = subprocess.run(
    ['git', 'log', '-1', '--format=%H'],
    stdout=subprocess.PIPE,
    text=True,
    cwd=main_path,
).stdout.strip()
papi_branch = subprocess.run(
    ['git', 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'],
    stdout=subprocess.PIPE,
    text=True,
    cwd=main_path,
).stdout.strip()
papi_branch = papi_branch.split('/')[-1]  # remove the "origin/" part
