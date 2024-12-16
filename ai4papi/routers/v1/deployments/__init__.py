import fastapi

from . import modules, tools


router = fastapi.APIRouter()
router.include_router(
    router=modules.router,
    prefix="/deployments",
)
router.include_router(
    router=tools.router,
    prefix="/deployments",
)
