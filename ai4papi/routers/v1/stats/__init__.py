import fastapi

from . import deployments


app = fastapi.APIRouter()
app.include_router(
    router=deployments.router,
    prefix='/deployments',
    )
