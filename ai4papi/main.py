"""
Create an app with FastAPI
"""

import fastapi
import uvicorn

from ai4papi.conf import MAIN_CONF, paths
from fastapi.responses import FileResponse
from ai4papi.routers import v1
from fastapi.middleware.cors import CORSMiddleware


description=(
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
)

app = fastapi.FastAPI(
    title="AI4EOSC Platform API",
    description=description,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=MAIN_CONF["auth"]["CORS_origins"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(v1.app, prefix="/v1")


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
        host:str = "0.0.0.0",
        port:int = 8080,
        ssl_keyfile:str = None,
        ssl_certfile:str = None,
    ):
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


if __name__ == "__main__":
    run()
