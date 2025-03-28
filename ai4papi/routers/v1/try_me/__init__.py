import fastapi

from . import nomad


router = fastapi.APIRouter()
router.include_router(
    router=nomad.router,
    prefix="/try_me",
)
