"""
Proxy to manage AI4LLM requests.
"""

import os
from typing import List

from openai import OpenAI

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from ai4papi import auth


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "ai4eoscassistant"
    messages: List[ChatMessage] = []


router = APIRouter(
    prefix="/ai4_llm",
    tags=["AI4LLM proxy"],
    responses={404: {"description": "AI4LLM not found"}},
)

security = HTTPBearer()

ROBOT_API_TOKEN = os.getenv("ROBOT_API_TOKEN")

client = OpenAI(
    base_url="https://llm.dev.ai4eosc.eu/api",
    api_key=ROBOT_API_TOKEN,
)


@router.post("/chat")
def get_chat_response(
    request: ChatRequest,
    vo: str,
    authorization=Depends(security),
):
    """
    Handle chat response, manage errors during completion creation
    """

    # Retrieve authenticated user info
    auth_info = auth.get_user_info(token=authorization.credentials)
    auth.check_vo_membership(vo, auth_info["vos"])

    try:
        completion = client.chat.completions.create(
            model=request.model, messages=request.messages
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {str(e)}"
        )

    return completion
