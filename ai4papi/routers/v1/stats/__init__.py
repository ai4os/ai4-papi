import fastapi

from . import deployments, modules


router = fastapi.APIRouter()
router.include_router(
    router=deployments.router,
    prefix="/stats",
)
router.include_router(
    router=modules.router,
    prefix="/stats",
)
