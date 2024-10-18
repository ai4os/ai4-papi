import fastapi
from . import snapshots

app = fastapi.APIRouter()
app.include_router(
    router=snapshots.router,
    prefix='/snapshots',
    )