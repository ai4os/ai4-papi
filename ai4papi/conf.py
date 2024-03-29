"""
Manage configurations of the API.
"""

from pathlib import Path
from string import Template

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
                raise Exception(
                    f"Parameter {k} needs to have a name."
                )
            if 'value' not in v.keys():
                raise Exception(
                    f"Parameter {k} needs to have a value."
                )
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
    }
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
        }
    }
