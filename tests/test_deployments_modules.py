import time
from types import SimpleNamespace

from ai4papi.routers.v1.deployments import modules
from ai4papi.routers.v1.deployments import tools
from conf import token


# Create module
rcreate = modules.create_deployment(
    vo="vo.ai4eosc.eu",
    conf={},
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rcreate, dict)
assert "job_ID" in rcreate.keys()

time.sleep(0.2)  # Nomad takes some time to allocate deployment

# Retrieve that module
rdep = modules.get_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdep, dict)
assert "job_ID" in rdep.keys()
assert rdep["job_ID"] == rcreate["job_ID"]
assert rdep["status"] != "error"

# Retrieve all modules
rdeps = modules.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdeps, list)
assert any([d["job_ID"] == rcreate["job_ID"] for d in rdeps])
assert all([d["job_ID"] != "error" for d in rdeps])

# Check that we cannot retrieve that module from tools
# This should break!
# tools.get_deployment(
#     vo='vo.ai4eosc.eu',
#     deployment_uuid=rcreate['job_ID'],
#     authorization=SimpleNamespace(
#         credentials=token
#     ),
# )

# Check that we cannot retrieve that module from tools list
rdeps2 = tools.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdeps2, list)
assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps2])

# Delete module
rdel = modules.delete_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdel, dict)
assert "status" in rdel.keys()

time.sleep(3)  # Nomad takes some time to delete

# Check module no longer exists
rdeps3 = modules.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps3])

# Check that we are able to retrieve info from Nomad snapshots (provenance)
modules.provenance_token = "1234"
r_prov = modules.get_deployment(
    vo="",
    deployment_uuid="de0599d6-a1b9-11ef-b98d-0242ac120005",
    authorization=SimpleNamespace(credentials="1234"),
)

print("🟢 Deployments (modules) tests passed!")
