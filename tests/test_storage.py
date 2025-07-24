from types import SimpleNamespace

from ai4papi.routers.v1 import storage
from conf import token


r = storage.storage_ls(
    vo="vo.ai4eosc.eu",
    storage_name="share.services.ai4os.eu",
    subpath="ai4os-storage",
    authorization=SimpleNamespace(credentials=token),
)

print("🟢 Storage tests passed!")
