import os
from types import SimpleNamespace

from ai4papi.routers.v1.inference import oscar

# Retrieve EGI token (not generated on the fly in case the are rate limitng issues
# if too many queries)
token = os.getenv("TMP_EGI_TOKEN")
if not token:
    raise Exception(
        'Please remember to set a token as ENV variable before executing \
the tests! \n\n \
   export TMP_EGI_TOKEN="$(oidc-token egi-checkin-demo)" \n\n \
If running from VScode make sure to launch `code` from that terminal so it can access \
that ENV variable.'
    )

# Test service
service = oscar.Service(
    image="deephdc/deep-oc-image-classification-tf",
    cpu=2,
)

# Create service
sname = oscar.create_service(
    vo="vo.ai4eosc.eu",
    svc_conf=service,
    authorization=SimpleNamespace(credentials=token),
)

# Check service exists
slist = oscar.get_services_list(
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
names = [s["name"] for s in slist]
assert sname in names, "Service does not exist"

# Update service
service.cpu = 1
oscar.update_service(
    vo="vo.ai4eosc.eu",
    service_name=sname,
    svc_conf=service,
    authorization=SimpleNamespace(credentials=token),
)

# Delete the service
oscar.delete_service(
    vo="vo.ai4eosc.eu",
    service_name=sname,
    authorization=SimpleNamespace(credentials=token),
)

# Check service does not longer exist
slist = oscar.get_services_list(
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
names = [s["name"] for s in slist]
assert sname not in names, "Service exists"

print("Inference (OSCAR) tests passed!")
