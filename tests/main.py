"""
Notes
=====
* Deployments tests
Because we have to use safe_substitute(), we are not catching errors that occur
when template variables have typo (eg. ${PCU_NUM}) because those only raise error in
Nomad (ie. after launching)
"""

# TODO: move to proper testing package
# TODO: add spinners

from pathlib import Path

import ai4papi.conf as papiconf


# We want to test full functionality, without disabling any parts
papiconf.IS_DEV = False

# Import all test files dynamically
test_files = sorted(Path(__file__).parent.glob("test_*.py"))
modules = [f.stem for f in test_files]

# Ignore tests that have already passed in a previous run
resume_from = None
# resume_from = "test_routes"
if resume_from:
    modules = modules[modules.index(resume_from) :]

for module in modules:
    __import__(module)
