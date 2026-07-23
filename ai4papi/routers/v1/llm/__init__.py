import fastapi

from . import chat, keys

router = fastapi.APIRouter()

router.include_router(
    router=chat.router,
    prefix="/llm",
)

router.include_router(
    router=keys.router,
    prefix="/llm",
)
