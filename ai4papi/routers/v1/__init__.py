"""V1 routes."""

import fastapi

from . import deployments, info, modules


app = fastapi.APIRouter()
app.include_router(deployments.router)
app.include_router(info.router)
app.include_router(modules.router)


@app.get(
    "/",
    summary="Get v1 version information.",
    tags=["API", "version"],
)
def get_version(request: fastapi.Request):
    """Get V1 version information."""
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
