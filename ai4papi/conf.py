"""
Manage configurations of the API.
"""

from pathlib import Path

import nomad
import yaml


Nomad = nomad.Nomad()

# Paths
main_path = Path(__file__).parent.absolute()
paths = {
    "conf": main_path.parent / "etc",
    "media": main_path / "media",
    }

# Load main API configuration
with open(paths['conf'] / 'main.yaml', 'r') as f:
    MAIN_CONF = yaml.safe_load(f)

####################
# Standard modules #
####################

# Load default Nomad job configuration
with open(paths['conf'] / 'module' / 'module.nomad', 'r') as f:
    raw_job = f.read()
    NOMAD_MODULE_CONF = Nomad.jobs.parse(raw_job)

# Load user customizable parameters
with open(paths['conf'] / 'module' / 'module.yaml', 'r') as f:
    USER_MODULE_CONF = yaml.safe_load(f)

USER_MODULE_VALUES = {}
for group_name, params in USER_MODULE_CONF.items():
    USER_MODULE_VALUES[group_name] = {}
    for k, v in params.items():
        assert 'name' in v.keys(), f"Parameter {k} needs to have a name."
        assert 'value' in v.keys(), f"Parameter {k} needs to have a value."

        USER_MODULE_VALUES[group_name][k] = v['value']

####################
# Federated server #
####################

# Load default Nomad job configuration
with open(paths['conf'] / 'federated' / 'federated.nomad', 'r') as f:
    raw_job = f.read()
    NOMAD_FED_CONF = Nomad.jobs.parse(raw_job)

# Load user customizable parameters
with open(paths['conf'] / 'federated' / 'federated.yaml', 'r') as f:
    USER_FED_CONF = yaml.safe_load(f)

USER_FED_VALUES = {}
for group_name, params in USER_FED_CONF.items():
    USER_FED_VALUES[group_name] = {}
    for k, v in params.items():
        assert 'name' in v.keys(), f"Parameter {k} needs to have a name."
        assert 'value' in v.keys(), f"Parameter {k} needs to have a value."

        USER_FED_VALUES[group_name][k] = v['value']
