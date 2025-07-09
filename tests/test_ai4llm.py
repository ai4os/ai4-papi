from types import SimpleNamespace

import openai

from ai4papi.routers.v1.proxies import ai4_llm
from conf import token


message = ai4_llm.ChatMessage(role="user", content="Hello")
request = ai4_llm.ChatRequest(messages=[message])
r = ai4_llm.get_chat_response(
    request=request,
    vo="vo.ai4eosc.eu",
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(r, openai.types.chat.chat_completion.ChatCompletion)

print("🟢 Proxies (ai4_llm) tests passed!")
