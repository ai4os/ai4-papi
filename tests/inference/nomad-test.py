import os
import time
from types import SimpleNamespace

from ai4papi.routers.v1.inference import nomad as inferences


# Retrieve EGI token (not generated on the fly in case the are rate limitng issues
# if too many queries)
token = os.getenv('TMP_EGI_TOKEN')
if not token:
    raise Exception(
'Please remember to set a token as ENV variable before executing \
the tests! \n\n \
   export TMP_EGI_TOKEN="$(oidc-token egi-checkin-demo)" \n\n \
If running from VScode make sure to launch `code` from that terminal so it can access \
that ENV variable.'
        )

# Create inference
rcreate = inferences.create_inference(
    vo='vo.ai4eosc.eu',
    authorization=SimpleNamespace(
        credentials=token
    ),
)
assert isinstance(rcreate, dict)
assert 'job_ID' in rcreate.keys()

# Retrieve that inference
rdep = inferences.get_inference(
    vo='vo.ai4eosc.eu',
    deployment_uuid=rcreate['job_ID'],
    authorization=SimpleNamespace(
        credentials=token
    ),
)
assert isinstance(rdep, dict)
assert 'job_ID' in rdep.keys()
assert rdep['job_ID']==rcreate['job_ID']

# Retrieve all inferences
rdeps = inferences.get_inferences(
    vos=['vo.ai4eosc.eu'],
    authorization=SimpleNamespace(
        credentials=token
    ),
)
assert isinstance(rdeps, list)
assert any([d['job_ID']==rcreate['job_ID'] for d in rdeps])

# Delete inference
rdel = inferences.delete_inference(
    vo='vo.ai4eosc.eu',
    deployment_uuid=rcreate['job_ID'],
    authorization=SimpleNamespace(
        credentials=token
    ),
)
time.sleep(3)  # Nomad takes some time to delete
assert isinstance(rdel, dict)
assert 'status' in rdel.keys()

# Check inference no longer exists
rdeps3 = inferences.get_inferences(
    vos=['vo.ai4eosc.eu'],
    authorization=SimpleNamespace(
        credentials=token
    ),
)
assert not any([d['job_ID']==rcreate['job_ID'] for d in rdeps3])

print('Inference (temporal modules) tests passed!')
