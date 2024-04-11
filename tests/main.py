"""
Notes
=====
* Deployments tests
Because we have to use safe_substitute(), we are not catching errors that occur
when template variables have typo (eg. ${PCU_NUM}) because those only raise error in
Nomad (ie. after launching)
"""

#TODO: move to proper testing package
#TODO: rename test script: modules --> test_modules

import catalog.modules
import catalog.tools
import deployments.modules
import deployments.tools
import routes
import test_secrets
