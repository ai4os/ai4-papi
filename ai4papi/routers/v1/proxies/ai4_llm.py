"""
Proxy to manage AI4LLM requests.
"""

import os
from typing import List

from openai import OpenAI

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

from ai4papi import auth


router = APIRouter(
    prefix="/ai4_llm",
    tags=["Proxies (AI4OS LLM)"],
    responses={404: {"description": "AI4LLM not found"}},
)

security = HTTPBearer()

#  Init the OpenAI client
LLM_API_KEY = os.getenv("LLM_API_KEY")
if LLM_API_KEY:
    client = OpenAI(
        base_url="https://llm.dev.ai4eosc.eu/api",
        api_key=LLM_API_KEY,
    )
else:
    client = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "ai4eoscassistant"
    messages: List[ChatMessage] = []


@router.post("")
def get_chat_response(
    request: ChatRequest,
    vo: str,
    authorization=Depends(security),
):
    """
    Handle chat response, manage errors during completion creation
    """

    # Retrieve authenticated user info
    # We allow anyone with an account, for the time being (group: demo)
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_authorization(auth_info, requested_vo="demo")

    try:
        completion = client.chat.completions.create(
            model=request.model, messages=request.messages, stream=True
        )

        def event_stream():
            for chunk in completion:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if not delta or not hasattr(delta, "content"):
                    continue
                content = delta.content or ""
                yield f"{content}"

        return StreamingResponse(event_stream(), media_type="text/plain")

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {str(e)}"
        )
