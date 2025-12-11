from types import SimpleNamespace

from ai4papi.routers.v1 import stats
from conf import token


SECRET_PATH = "/demo-papi-tests/demo-secret"
SECRET_DATA = {"pwd": 12345}

# Retrieve user stats
r = stats.deployments.get_user_stats(
    vo="ai4eosc",
    authorization=SimpleNamespace(credentials=token),
)
assert r, "User stats dict is empty"

# Retrieve cluster stats
_ = stats.deployments.get_cluster_stats_bg()
r = stats.deployments.get_cluster_stats(
    vo="ai4eosc",
)
assert r, "Cluster stats dict is empty"

print("🟢 Stats tests passed!")
