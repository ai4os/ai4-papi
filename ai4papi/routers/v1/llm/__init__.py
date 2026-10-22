import fastapi

from . import chat, keys, mcp

router = fastapi.APIRouter()

router.include_router(
    router=chat.router,
    prefix="/llm",
)

router.include_router(
    router=keys.router,
    prefix="/llm",
)

router.include_router(
    router=mcp.router,
    prefix="/llm",
)
