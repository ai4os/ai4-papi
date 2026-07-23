import fastapi

from . import modules, tools


router = fastapi.APIRouter()
router.include_router(
    router=modules.router,
    prefix="/catalog",
)
router.include_router(
    router=tools.router,
    prefix="/catalog",
)
