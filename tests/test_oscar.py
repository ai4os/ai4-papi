from types import SimpleNamespace

from ai4papi.routers.v1.inference import oscar
from conf import token


# Create service
sname = oscar.create_service(
    vo="ai4eosc",
    conf={},
    authorization=SimpleNamespace(credentials=token),
)

# Check service exists
slist = oscar.get_services_list(
    vo="ai4eosc",
    authorization=SimpleNamespace(credentials=token),
)
names = [s["name"] for s in slist]
assert sname in names, "Service does not exist"

# Update service
oscar.update_service(
    vo="ai4eosc",
    service_name=sname,
    conf={},
    authorization=SimpleNamespace(credentials=token),
)

# Delete the service
oscar.delete_service(
    vo="ai4eosc",
    service_name=sname,
    authorization=SimpleNamespace(credentials=token),
)

# Check service does not longer exist
slist = oscar.get_services_list(
    vo="ai4eosc",
    authorization=SimpleNamespace(credentials=token),
)
names = [s["name"] for s in slist]
assert sname not in names, "Service exists"

print("🟢 Inference (OSCAR) tests passed!")
