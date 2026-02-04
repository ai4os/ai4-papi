from types import SimpleNamespace

import asyncio
from starlette.responses import StreamingResponse

from ai4papi.routers.v1.llm import chat
from conf import token


# Generate a streaming response
message = chat.ChatMessage(role="user", content="Hello")
request = chat.ChatRequest(model="AI4EOSC/Qwen3", messages=[message])
r = chat.get_chat_response(
    request=request,
    vo="vo.ai4eosc.eu",
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

print("🟢 AI4OS LLM (chat) tests passed!")
