"""
Create an app with FastAPI
"""

from fastapi import Depends, FastAPI

from ai4eosc.routers import deployments, info, modules
# from ai4eosc.dependencies import get_query_token, get_token_header
# from .internal import admin


app = FastAPI(
    # dependencies=[Depends(get_query_token)],
)

app.include_router(deployments.router)
app.include_router(info.router)
app.include_router(modules.router)

# app.include_router(
#     admin.router,
#     prefix="/admin",
#     tags=["admin"],
#     dependencies=[Depends(get_token_header)],
#     responses={418: {"description": "I'm a teapot"}},
# )


@app.get("/")
async def root():
    return "This is the AI4EOSC project's API."
