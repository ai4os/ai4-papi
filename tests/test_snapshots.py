import os
import time
from types import SimpleNamespace

from ai4papi.routers.v1 import snapshots
from ai4papi.routers.v1.deployments import modules


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

# Create Nomad deployment
njob = modules.create_deployment(
    vo="vo.ai4eosc.eu",
    conf={},
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(njob, dict)
assert "job_ID" in njob.keys()

time.sleep(60)

# Make snapshot of that module
created = snapshots.create_snapshot(
    vo="vo.ai4eosc.eu",
    deployment_uuid=njob["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(created, dict)
assert "snapshot_ID" in created.keys()

time.sleep(10)

# Retrieve all snapshots
retrieved = snapshots.get_snapshots(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(retrieved, list)
assert any([d["snapshot_ID"] == created["snapshot_ID"] for d in retrieved])
# TODO: waiting 10s the snapshot is still probably queued in Nomad, we should wait more if we want to test also Harbor

# Delete snapshot
deleted = snapshots.delete_snapshot(
    vo="vo.ai4eosc.eu",
    snapshot_uuid=created["snapshot_ID"],
    authorization=SimpleNamespace(credentials=token),
)
time.sleep(10)  # it takes some time to delete
assert isinstance(deleted, dict)
assert "status" in deleted.keys()

# Check snapshot no longer exists
retrieved2 = snapshots.get_snapshots(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(retrieved, list)
assert not any([d["snapshot_ID"] == created["snapshot_ID"] for d in retrieved2])

# Delete deployment
ndel = modules.delete_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=njob["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(ndel, dict)
assert "status" in ndel.keys()


print("Snapshot tests passed!")
