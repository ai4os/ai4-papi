from types import SimpleNamespace

from ai4papi.routers.v1 import storage
from conf import token


r = storage.storage_ls(
    vo="ai4eosc",
    storage_name="share.cloud.ai4eosc.eu",
    subpath="ai4os-storage",
    authorization=SimpleNamespace(credentials=token),
)

print("🟢 Storage tests passed!")
