import fastapi
from . import snapshots


router = fastapi.APIRouter()
router.include_router(
    router=snapshots.router,
    prefix='/snapshots',
    )
