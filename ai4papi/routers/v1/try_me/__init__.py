import fastapi

from . import nomad


app = fastapi.APIRouter()
app.include_router(
    router=nomad.router,
    prefix='/try_me',
    )
