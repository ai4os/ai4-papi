import fastapi

from . import modules, tools, datasets, refresh


router = fastapi.APIRouter()
router.include_router(
    router=modules.router,
    prefix='/catalog',
)
router.include_router(
    router=tools.router,
    prefix='/catalog',
)
router.include_router(
    router=datasets.router,
    prefix='/datasets',
)

router.include_router(
    router=refresh.router,
    prefix='/catalog',
)
