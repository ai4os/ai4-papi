import fastapi

from . import zenodo


app = fastapi.APIRouter()
app.include_router(
    router=zenodo.router,
    )
