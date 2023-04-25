"""
Create an app with FastAPI
"""

import fastapi
from fastapi.security import HTTPBearer
import uvicorn

from ai4papi.auth import get_user_info
from ai4papi.routers import v1
from fastapi.middleware.cors import CORSMiddleware

app = fastapi.FastAPI()
origins = [
    "https://dashboard.dev.imagine-ai.eu",
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
    # authorization=fastapi.Depends(security),
):
    # Retrieve authenticated user info
    # auth_info = get_user_info(token=authorization.credentials)

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
        host:str = "0.0.0.0",
        port:int = 8080,
    ):
    uvicorn.run(
        app,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    run()
