import fastapi

from . import deployments


router = fastapi.APIRouter()
router.include_router(
    router=deployments.router,
    prefix="/deployments",
)
