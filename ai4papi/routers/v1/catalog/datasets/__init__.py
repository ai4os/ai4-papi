import fastapi

from . import zenodo


router = fastapi.APIRouter()
router.include_router(
    router=zenodo.router,
)
