import os
from types import SimpleNamespace

from ai4papi.routers.v1 import storage


# Retrieve EGI token (not generated on the fly in case the are rate limiting issues
# if too many queries)
token = os.getenv('TMP_EGI_TOKEN')
if not token:
    raise Exception(
'Please remember to set a token as ENV variable before executing \
the tests! \n\n \
   export TMP_EGI_TOKEN="$(oidc-token egi-checkin)" \n\n \
If running from VScode make sure to launch `code` from that terminal so it can access \
that ENV variable.'
        )

r = storage.storage_ls(
    vo='vo.ai4eosc.eu',
    storage_name='share.services.ai4os.eu',
    subpath='ai4os-storage',
    authorization=SimpleNamespace(
        credentials=token
    ),
)

print('Storage tests passed!')
