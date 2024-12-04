import os
from types import SimpleNamespace

from ai4papi.routers.v1 import secrets


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

SECRET_PATH = "/demo-papi-tests/demo-secret"
SECRET_DATA = {"pwd": 12345}

# Create secret
r = secrets.create_secret(
    vo="vo.ai4eosc.eu",
    secret_path=SECRET_PATH,
    secret_data=SECRET_DATA,
    authorization=SimpleNamespace(credentials=token),
)

# Check that secret is in list
r = secrets.get_secrets(
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
assert SECRET_PATH in r.keys()
assert r[SECRET_PATH] == SECRET_DATA

# Delete
r = secrets.delete_secret(
    vo="vo.ai4eosc.eu",
    secret_path=SECRET_PATH,
    authorization=SimpleNamespace(credentials=token),
)

# Check that secret is no longer in list
r = secrets.get_secrets(
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
assert SECRET_PATH not in r.keys()

print("Secrets tests passed!")
