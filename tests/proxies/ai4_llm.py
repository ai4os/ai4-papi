import os
from types import SimpleNamespace

import openai

from ai4papi.routers.v1.proxies import ai4_llm

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


message = ai4_llm.ChatMessage(role="user", content="Hello")
request = ai4_llm.ChatRequest(messages=[message])
r = ai4_llm.get_chat_response(
    request=request,
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(r, openai.types.chat.chat_completion.ChatCompletion)

print("🟢 Proxies (ai4_llm) tests passed!")
