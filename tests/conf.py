"""
Common configuration for tests
"""

import os
import subprocess


# Retrieve token with oidc-token command
try:
    result = subprocess.run(
        ["oidc-token", "ai4os-keycloak"], capture_output=True, text=True, check=True
    )
    token = result.stdout.strip()
except Exception:
    # Retrieve token as envvar instead
    # If running from VScode make sure to launch `code` from that terminal so it can access that ENV variable
    token = os.getenv("PAPI_TESTS_TOKEN")

if not token:
    raise Exception(
        "PAPI needs to retrieve an OIDC token either using oidc-agent or ENV variables."
    )
