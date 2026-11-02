from types import SimpleNamespace

from conf import token

from ai4papi.routers.v1 import storage

r = storage.storage_ls(
    vo="vo.ai4eosc.eu",
    storage_name="share.cloud.ai4eosc.eu",
    subpath="ai4os-storage",
    authorization=SimpleNamespace(credentials=token),
)

print("🟢 Storage tests passed!")
