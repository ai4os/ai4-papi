"""
Create an app with FastAPI
"""

from fastapi import Depends, FastAPI
from fastapi.security import HTTPBearer

from ai4eosc.auth import get_user_info
from ai4eosc.routers import deployments, info, modules


app = FastAPI()
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
