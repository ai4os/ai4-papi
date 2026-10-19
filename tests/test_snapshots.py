import asyncio
import time
from types import SimpleNamespace

from conf import token

from ai4papi.routers.v1 import snapshots
from ai4papi.routers.v1.deployments import modules

# Create Nomad deployment
njob = asyncio.run(
    modules.create_deployment(
        vo="vo.ai4eosc.eu",
        conf={},
        authorization=SimpleNamespace(credentials=token),
    )
)
assert isinstance(njob, dict)
assert "job_ID" in njob.keys()

# Poll until deployment is running
t0 = time.time()
timeout = 300
while time.time() - t0 < timeout:
    rdep = modules.get_deployment(
        vo="vo.ai4eosc.eu",
        deployment_uuid=njob["job_ID"],
        authorization=SimpleNamespace(credentials=token),
    )
    if rdep.get("status") == "running":
        break
    time.sleep(5)
else:
    raise TimeoutError(
        f"Deployment {njob['job_ID']} did not reach 'running' status within {timeout}s (last status: {rdep.get('status')})"
    )

# Make snapshot of that module
created = asyncio.run(
    snapshots.create_snapshot(
        vo="vo.ai4eosc.eu",
        deployment_uuid=njob["job_ID"],
        authorization=SimpleNamespace(credentials=token),
    )
)
assert isinstance(created, dict)
assert "snapshot_ID" in created.keys()

time.sleep(60)
# we sleep 60s to make sure snapshot is in Harbor. No need to test deleting from Nomad
# because the process is similar to deleting a standard module.

# Retrieve all snapshots
retrieved = asyncio.run(
    snapshots.get_snapshots(
        vos=["vo.ai4eosc.eu"],
        authorization=SimpleNamespace(credentials=token),
    )
)
assert isinstance(retrieved, list)
assert any([d["snapshot_ID"] == created["snapshot_ID"] for d in retrieved])

# Delete snapshot
deleted = asyncio.run(
    snapshots.delete_snapshot(
        vo="vo.ai4eosc.eu",
        snapshot_uuid=created["snapshot_ID"],
        authorization=SimpleNamespace(credentials=token),
    )
)
time.sleep(10)  # it takes some time to delete
assert isinstance(deleted, dict)
assert "status" in deleted.keys()

# Check snapshot no longer exists
retrieved2 = asyncio.run(
    snapshots.get_snapshots(
        vos=["vo.ai4eosc.eu"],
        authorization=SimpleNamespace(credentials=token),
    )
)
assert isinstance(retrieved2, list)
assert not any([d["snapshot_ID"] == created["snapshot_ID"] for d in retrieved2])

# Delete deployment
ndel = modules.delete_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=njob["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(ndel, dict)
assert "status" in ndel.keys()


print("🟢 Snapshot tests passed!")
