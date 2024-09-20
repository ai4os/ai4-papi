import fastapi

from . import catalog, deployments, inference, secrets, stats, try_me


app = fastapi.APIRouter()
app.include_router(catalog.app)
app.include_router(deployments.app)
app.include_router(inference.app)
app.include_router(secrets.router)
app.include_router(stats.app)
app.include_router(try_me.app)


@app.get(
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
