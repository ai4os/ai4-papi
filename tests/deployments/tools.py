import os
import time
from types import SimpleNamespace

from ai4papi.routers.v1.deployments import modules
from ai4papi.routers.v1.deployments import tools


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


# Only use mandatory config parameters, otherwise use defaults
tools_config = {
    "ai4os-federated-server": {},
    "ai4os-cvat": {
        "general": {
            "cvat_username": "mock_user",
            "cvat_password": "mock_password",
        },
        "storage": {
            "rclone_conf": "/srv/.rclone/rclone.conf",
            "rclone_url": "https://share.services.ai4os.eu/remote.php/webdav",
            "rclone_vendor": "nextcloud",
            "rclone_user": "mock_user",
            "rclone_password": "mock_password",
        },
    },
    "ai4os-ai4life-loader": {
        "general": {
            "model_id": "happy-elephant",
        },
    },
    "ai4os-llm": {
        "llm": {
            "model_id": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
            "ui_password": "papi-test",
        },
    },
    "ai4os-nvflare": {
        "nvflare": {
            "username": "mock_user",
            "password": "mock_password",
        },
    },
}

for tname, tconfig in tools_config.items():
    print(f"  Testing tool: {tname}")

    # Create tool
    rcreate = tools.create_deployment(
        vo="vo.ai4eosc.eu",
        tool_name=tname,
        conf=tconfig,
        authorization=SimpleNamespace(credentials=token),
    )
    assert isinstance(rcreate, dict)
    assert "job_ID" in rcreate.keys()

    time.sleep(0.2)  # Nomad takes some time to allocate deployment

    # Retrieve that tool
    rdep = tools.get_deployment(
        vo="vo.ai4eosc.eu",
        deployment_uuid=rcreate["job_ID"],
        authorization=SimpleNamespace(credentials=token),
    )
    assert isinstance(rdep, dict)
    assert "job_ID" in rdep.keys()
    assert rdep["job_ID"] == rcreate["job_ID"]
    assert rdep["status"] != "error"
    assert rdep["tool_name"]

    # Retrieve all tools
    rdeps = tools.get_deployments(
        vos=["vo.ai4eosc.eu"],
        authorization=SimpleNamespace(credentials=token),
    )
    assert isinstance(rdeps, list)
    assert any([d["job_ID"] == rcreate["job_ID"] for d in rdeps])
    assert all([d["job_ID"] != "error" for d in rdeps])

    # Check that we cannot retrieve that tool from modules
    # This should break!
    # modules.get_deployment(
    #     vo='vo.ai4eosc.eu',
    #     deployment_uuid=rcreate['job_ID'],
    #     authorization=SimpleNamespace(
    #         credentials=token
    #     ),
    # )

    # Check that we cannot retrieve that tool from modules list
    rdeps2 = modules.get_deployments(
        vos=["vo.ai4eosc.eu"],
        authorization=SimpleNamespace(credentials=token),
    )
    assert isinstance(rdeps2, list)
    assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps2])

    # Delete tool
    rdel = tools.delete_deployment(
        vo="vo.ai4eosc.eu",
        deployment_uuid=rcreate["job_ID"],
        authorization=SimpleNamespace(credentials=token),
    )
    assert isinstance(rdel, dict)
    assert "status" in rdel.keys()

    time.sleep(3)  # Nomad takes some time to delete

    # Check tool no longer exists
    rdeps3 = tools.get_deployments(
        vos=["vo.ai4eosc.eu"],
        authorization=SimpleNamespace(credentials=token),
    )
    assert not any([d["job_ID"] == rcreate["job_ID"] for d in rdeps3])

print("Deployments (tools) tests passed!")
