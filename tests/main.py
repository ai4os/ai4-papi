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

import glob
import os

import ai4papi.conf as papiconf


# We want to test full functionality, without disabling any parts
papiconf.IS_DEV = False


# Import all test files dynamically
test_files = glob.glob(os.path.join(os.path.dirname(__file__), "test_*.py"))
test_files.sort()
for test_file in test_files:
    module_name = os.path.basename(test_file)[:-3]  # Remove .py extension
    __import__(module_name)
