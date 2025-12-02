import fastapi

from . import ai4_llm, zenodo, apisix

router = fastapi.APIRouter()

router.include_router(
    router=ai4_llm.router,
    prefix="/proxies",
)

router.include_router(
    router=zenodo.router,
    prefix="/proxies",
)

router.include_router(
    router=apisix.router,
    prefix="/proxies",
)