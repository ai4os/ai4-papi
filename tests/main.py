"""
Notes
=====
* Deployments tests
Because we have to use safe_substitute(), we are not catching errors that occur
when template variables have typo (eg. ${PCU_NUM}) because those only raise error in
Nomad (ie. after launching)
"""

# TODO: move to proper testing package
# TODO: rename test script: modules --> test_modules
# TODO: add spinners

import ai4papi.conf as papiconf


# We want to test full functionality, without disabling any parts
papiconf.IS_DEV = False


import catalog.modules
import catalog.tools
import deployments.modules
import deployments.tools
import try_me.test_nomad
import routes
import test_secrets
import test_stats
import test_storage
import test_launch
