from types import SimpleNamespace

from ai4papi.routers.v1 import secrets
from conf import token


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

print("🟢 Secrets tests passed!")
