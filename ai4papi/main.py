"""
Create an app with FastAPI
"""

from fastapi import Depends, FastAPI
from fastapi.security import HTTPBearer
import uvicorn

from ai4papi.auth import get_user_info
from ai4papi.routers import deployments, info, modules
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
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

app.include_router(deployments.router)
app.include_router(info.router)
app.include_router(modules.router)

security = HTTPBearer()


@app.get("/")
def root(
    authorization=Depends(security),
    ):
    # Retrieve authenticated user info
    auth_info = get_user_info(token=authorization.credentials)

    return f"This is the AI4EOSC project's API. Current authenticated user: {auth_info}"


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
