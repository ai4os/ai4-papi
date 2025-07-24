import time
from types import SimpleNamespace

from ai4papi.routers.v1.try_me import nomad
from conf import token


# Create deployment
rcreate = nomad.create_deployment(
    module_name="ai4os-demo-app",
    title="PAPI tests",
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rcreate, dict)
assert "job_ID" in rcreate.keys()

# Retrieve that deployment
rdep = nomad.get_deployment(
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdep, dict)
assert "job_ID" in rdep.keys()
assert rdep["job_ID"] == rcreate["job_ID"]

# Retrieve all deployments
rdeps = nomad.get_deployments(
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdeps, list)
assert any([d["job_ID"] == rcreate["job_ID"] for d in rdeps])

# Delete deployment
rdel = nomad.delete_deployment(
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
time.sleep(3)  # Nomad takes some time to delete
assert isinstance(rdel, dict)
assert "status" in rdel.keys()

# Check module no longer exists
rdeps3 = nomad.get_deployments(
    authorization=SimpleNamespace(credentials=token),
)
assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps3])

print("🟢 Try-me (nomad) tests passed!")
