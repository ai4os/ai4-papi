import tempfile
import time
from types import SimpleNamespace

from fastapi import UploadFile

from ai4papi.routers.v1 import batch
from conf import token


# Create a job
batch_file = tempfile.SpooledTemporaryFile()
batch_file.write(b"""
echo "Test started"
date > /storage/test-batch.txt
sleep 6000
""")
batch_file.seek(0)
rcreate = batch.create_deployment(
    vo="vo.ai4eosc.eu",
    user_cmd=UploadFile(file=batch_file),
    conf="""
    {
        "general": {
            "docker_image": "ai4oshub/ai4os-demo-app"
        },
        "storage": {
            "rclone_conf": "/srv/.rclone/rclone.conf",
            "rclone_url": "https://share.services.ai4os.eu/remote.php/dav/files/EGI_Checkin-0000000000000000000000000000000000",
            "rclone_vendor": "nextcloud",
            "rclone_user": "EGI_Checkin-0000000000000000000000000000000000",
            "rclone_password": "0000000000000000000000000000"
        }
    }
    """,
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rcreate, dict)
assert "job_ID" in rcreate.keys()

time.sleep(0.2)  # Nomad takes some time to allocate deployment

# Retrieve that job
rdep = batch.get_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=rcreate["job_ID"],
    full_info=True,
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdep, dict)
assert "job_ID" in rdep.keys()
assert rdep["job_ID"] == rcreate["job_ID"]
assert rdep["status"] != "error"
assert "templates" in rdep  # user batch script

# Retrieve all jobs
rdeps = batch.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdeps, list)
assert any([d["job_ID"] == rcreate["job_ID"] for d in rdeps])
assert all([d["job_ID"] != "error" for d in rdeps])

# Delete job
rdel = batch.delete_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdel, dict)
assert "status" in rdel.keys()

time.sleep(3)  # Nomad takes some time to delete

# Check job no longer exists
rdeps3 = batch.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps3])

print("🟢 Batch jobs (Nomad) tests passed!")
