import fastapi

from . import ai4_llm


router = fastapi.APIRouter()
router.include_router(
    router=ai4_llm.router,
    prefix="/proxies",
)
