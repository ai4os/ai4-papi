"""
Create an app with FastAPI
"""

from contextlib import asynccontextmanager
import fastapi
import uvicorn

from ai4papi.conf import MAIN_CONF, paths, papi_branch, papi_commit
from fastapi.responses import FileResponse
from ai4papi.routers import v1
from ai4papi.routers.v1.stats.deployments import get_cluster_stats_bg
from fastapi.middleware.cors import CORSMiddleware
from fastapi_utils.tasks import repeat_every


description = (
    "<img"
    " src='https://ai4eosc.eu/wp-content/uploads/sites/10/2023/01/horizontal-bg-dark.png'"
    " width=200 alt='' />"
    "<br><br>"
    "This is the Platform API for interacting with the AI4EOSC services. "
    "It aims at providing a stable UI, effectively decoupling the services offered by "
    "the project from the underlying tools we use to provide them (ie. Nomad)."
    "<br><br>"
    "You can also access the functionalities of the API through our dashboards: <br>"
    "- [AIEOSC Dashboard](https://dashboard.cloud.ai4eosc.eu/) <br>"
    "- [iMagine Dashboard](https://dashboard.cloud.imagine-ai.eu/)"
    "<br><br>"
    "For more information, please visit: <br>"
    "- [AI4EOSC Homepage](https://ai4eosc.eu) <br>"
    "- [API Github repository](https://github.com/AI4EOSC/ai4-papi)"
    "<br><br>"
    "**Acknowledgements** <br>"
    "This work is co-funded by [AI4EOSC](https://ai4eosc.eu/) project that has "
    "received funding from the European Union's Horizon Europe 2022 research and "
    "innovation programme under agreement No 101058593"
    "<br><br>"
    "PAPI version:"
    f"[`ai4-papi/{papi_branch}@{papi_commit[:5]}`]"
    f"(https://github.com/ai4os/ai4-papi/tree/{papi_commit})"
)


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    # on startup
    await get_cluster_stats_thread()
    yield
    # on shutdown
    # (nothing to do)


app = fastapi.FastAPI(
    title="AI4EOSC Platform API",
    description=description,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=MAIN_CONF["auth"]["CORS_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(v1.router, prefix="/v1")


@app.get(
    "/",
    summary="Get AI4EOSC Platform API Information",
    tags=["API"],
)
def root(
    request: fastapi.Request,
):
    root = str(request.url_for("root"))
    versions = [v1.get_version(request)]

    response = {
        "versions": versions,
        "links": [
            {
                "rel": "help",
                "type": "text/html",
                "href": f"{root}" + app.docs_url.strip("/"),
            },
            {
                "rel": "help",
                "type": "text/html",
                "href": f"{root}" + app.redoc_url.strip("/"),
            },
            {
                "rel": "describedby",
                "type": "application/json",
                "href": f"{root}" + app.openapi_url.strip("/"),
            },
        ],
    }
    return response


@app.get(
    "/favicon.ico",
    include_in_schema=False,
)
async def favicon():
    return FileResponse(paths["media"] / "cropped-favicon-32x32.png")


def run(
    host: str = "0.0.0.0",
    port: int = 8080,
    ssl_keyfile: str = None,
    ssl_certfile: str = None,
):
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


# Compute cluster stats in background task
@repeat_every(seconds=30)
def get_cluster_stats_thread():
    """
    Recompute cluster stats
    Do *not* run as async to avoid blocking the main event.
    ref: https://stackoverflow.com/questions/67599119/fastapi-asynchronous-background-tasks-blocks-other-requests
    """
    get_cluster_stats_bg.cache_clear()
    get_cluster_stats_bg()


if __name__ == "__main__":
    run()
