import fastapi

from . import oscar


router = fastapi.APIRouter()
router.include_router(
    router=oscar.router,
    prefix='/inference',
    )
