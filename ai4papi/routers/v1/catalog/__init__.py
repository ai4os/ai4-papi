import fastapi

from . import modules, tools, llms_self, llms_platform


router = fastapi.APIRouter()
router.include_router(
    router=modules.router,
    prefix="/catalog",
)
router.include_router(
    router=tools.router,
    prefix="/catalog",
)
router.include_router(router=llms_self.router, prefix="/catalog")
router.include_router(router=llms_platform.router, prefix="/catalog")
