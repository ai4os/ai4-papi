from types import SimpleNamespace

import asyncio
from starlette.responses import StreamingResponse

from ai4papi.routers.v1.proxies import ai4_llm
from conf import token


# Generate a streaming response
message = ai4_llm.ChatMessage(role="user", content="Hello")
request = ai4_llm.ChatRequest(model="AI4EOSC/Qwen3", messages=[message])
r = ai4_llm.get_chat_response(
    request=request,
    vo="ai4eosc",
    authorization=SimpleNamespace(credentials=token),
)
assert isinstance(r, StreamingResponse)


# Try retrieving the streamed response
async def get_full_response(response):
    body = ""
    async for chunk in response.body_iterator:
        body += chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    return body


body = asyncio.run(get_full_response(r))
assert body
assert isinstance(body, str)

print("🟢 Proxies (ai4_llm) tests passed!")
