"""
Create an app with FastAPI
"""

from fastapi import Depends, FastAPI, Request, Response

from ai4eosc.routers import deployments, info, modules
# from ai4eosc.dependencies import get_query_token, get_token_header
# from .internal import admin
from fastapi.security import HTTPBearer
from flaat_impl import init_flaat, flaat, get_owner



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

security = HTTPBearer()
init_flaat()

@app.get("/")
@flaat.is_authenticated()
async def root(request: Request):
    sub, iss = get_owner(request)
    return "This is the AI4EOSC project's API currently used by {}@{}.".format(a, sub, iss)

# -------------------------------------------------------------------
# Main function -----------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
