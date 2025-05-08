import os
import tempfile
import time
from types import SimpleNamespace

from fastapi import UploadFile

from ai4papi.routers.v1 import batch


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

# Create a module
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

# Retrieve that module
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

# Retrieve all modules
rdeps = batch.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdeps, list)
assert any([d["job_ID"] == rcreate["job_ID"] for d in rdeps])
assert all([d["job_ID"] != "error" for d in rdeps])

# Delete module
rdel = batch.delete_deployment(
    vo="vo.ai4eosc.eu",
    deployment_uuid=rcreate["job_ID"],
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(rdel, dict)
assert "status" in rdel.keys()

time.sleep(3)  # Nomad takes some time to delete

# Check module no longer exists
rdeps3 = batch.get_deployments(
    vos=["vo.ai4eosc.eu"],
    authorization=SimpleNamespace(credentials=token),
)
assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps3])

print("Batch jobs (Nomad) tests passed!")
