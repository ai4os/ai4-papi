"""
Manage configurations of the module.
"""
from pathlib import Path

import nomad
import yaml


Nomad = nomad.Nomad()

CONF_DIR = Path(__file__).parents[1].absolute() / 'etc'

# Load default Nomad job configuration
with open(CONF_DIR / 'job.nomad', 'r') as fh:
    job_raw_nomad = fh.read()
    NOMAD_JOB_CONF = Nomad.jobs.parse(job_raw_nomad)

# Load user customizable parameters
with open(CONF_DIR / 'userconf.yaml', 'r') as f:
    USER_CONF = yaml.safe_load(f)

USER_CONF_VALUES = {}
for group_name, params in USER_CONF.items():
    USER_CONF_VALUES[group_name] = {}
    for k, v in params.items():
        assert 'name' in v.keys(), f"Parameter {k} needs to have a name."
        assert 'value' in v.keys(), f"Parameter {k} needs to have a value."

        USER_CONF_VALUES[group_name][k] = v['value']

