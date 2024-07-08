import fastapi

from . import modules, tools, datasets


app = fastapi.APIRouter()
app.include_router(
    router=modules.router,
    prefix='/catalog',
    )
app.include_router(
    router=tools.router,
    prefix='/catalog',
    )
app.include_router(
    router=datasets.app,
    prefix='/datasets',
    )
