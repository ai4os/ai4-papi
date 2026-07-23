from types import SimpleNamespace


from ai4papi.routers.v1.llm import keys
from conf import token

# Define test key name
KEYNAME = "papi-tests"


# Retrieve api keys list
k1 = keys.get_api_keys(
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(k1, list)

# Delete old keys of past tests, if any
if any([k["id"] == KEYNAME for k in k1]):
    _ = keys.delete_api_key(
        KEYNAME,
        authorization=SimpleNamespace(credentials=token),
    )

# Create new key
new = keys.create_api_key(
    key_name=KEYNAME,
    authorization=SimpleNamespace(credentials=token),
)

# Check that the key can be retrieved
k2 = keys.get_api_keys(
    authorization=SimpleNamespace(credentials=token),
)
assert any([k["id"] == KEYNAME for k in k2])

# Delete the key
_ = keys.delete_api_key(
    KEYNAME,
    authorization=SimpleNamespace(credentials=token),
)

# Check that the key is no longer there
k3 = keys.get_api_keys(
    authorization=SimpleNamespace(credentials=token),
)
assert not any([k["id"] == KEYNAME for k in k3])

print("🟢 AI4OS LLM (keys) tests passed!")
