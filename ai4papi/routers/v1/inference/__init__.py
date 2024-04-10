import fastapi

from . import oscar


app = fastapi.APIRouter()
app.include_router(
    router=oscar.router,
    prefix='/inference',
    )
