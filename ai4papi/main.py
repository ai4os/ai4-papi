"""AI4EOSC Plaform API (AI4PAPI) with FastAPI."""

import ipaddress
import typing

import fastapi
from fastapi.security import HTTPBearer
import typer
import uvicorn

from ai4papi.routers import v1
from fastapi.middleware.cors import CORSMiddleware


app = fastapi.FastAPI()
origins = [
    "https://dashboard.dev.imagine-ai.eu",
    "https://dashboard.dev.imagine-ai.eu:8443",
    "https://dashboard.cloud.imagine-ai.eu",
    "https://dashboard.dev.ai4eosc.eu",
    "https://dashboard.cloud.ai4eosc.eu",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
app.include_router(v1.app, prefix="/v1")


@app.get(
    "/",
    summary="Get AI4EOSC Platform API Information",
    tags=["API"],
)
def root(
    request: fastapi.Request,
):
    """Get version and documentation endpoints."""
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


def run(
    host: ipaddress.IPv4Address = ipaddress.IPv4Address("127.0.0.1"),  # noqa(B008)
    port: int = 8080,
    ssl_keyfile: typing.Optional[pathlib.Path] = typer.Option(None),  # noqa(B008)
    ssl_certfile: typing.Optional[pathlib.Path] = typer.Option(None),  # noqa(B008)
):
    """Run the API in uvicorn."""
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


if __name__ == "__main__":
    run()
