import os
from types import SimpleNamespace

from ai4papi.routers.v1 import stats


# Retrieve EGI token (not generated on the fly in case the are rate limiting issues
# if too many queries)
token = os.getenv("TMP_EGI_TOKEN")
if not token:
    raise Exception(
        'Please remember to set a token as ENV variable before executing \
the tests! \n\n \
   export TMP_EGI_TOKEN="$(oidc-token egi-checkin)" \n\n \
If running from VScode make sure to launch `code` from that terminal so it can access \
that ENV variable.'
    )

SECRET_PATH = "/demo-papi-tests/demo-secret"
SECRET_DATA = {"pwd": 12345}

# Retrieve user stats
r = stats.deployments.get_user_stats(
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
assert r, "User stats dict is empty"

# Retrieve cluster stats
_ = stats.deployments.get_cluster_stats_bg()
r = stats.deployments.get_cluster_stats(
    vo="vo.ai4eosc.eu",
)
assert r, "Cluster stats dict is empty"

print("Stats tests passed!")
