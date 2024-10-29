import fastapi

from . import catalog, deployments, inference, secrets, stats, storage, try_me


router = fastapi.APIRouter()
router.include_router(catalog.router)
router.include_router(deployments.router)
router.include_router(inference.router)
router.include_router(secrets.router)
router.include_router(stats.router)
router.include_router(storage.router)
router.include_router(try_me.router)


@router.get(
    "/",
    summary="Get v1 version information.",
    tags=["API", "version"],
)
def get_version(request: fastapi.Request):
    root = str(request.url_for("get_version"))
    # root = "/"
    version = {
        "version": "stable",
        "id": "v1",
        "links": [
            {
                "rel": "self",
                "type": "application/json",
                "href": f"{root}",
            },
        ],
    }
    return version
