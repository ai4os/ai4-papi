from fastapi import APIRouter
from fastapi import HTTPException

from ai4papi.routers.v1.catalog import tools, modules


def refresh_cache(item_name: str):
    # Check if item is a tool or a module

    if item_name in modules.Modules.get_items().keys():
        try:
            modules.Modules.refresh_metadata_cache_entry(item_name)
            return {"message": "Cache refreshed successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    elif item_name in tools.Tools.get_items().keys():
        try:
            tools.Tools.refresh_metadata_cache_entry(item_name)
            return {"message": "Cache refreshed successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(
            status_code=400,
            detail=f"{item_name} is not an available tool or module.",
        )


router = APIRouter(
    prefix="/refresh",
    tags=["Catalog"],
    responses={404: {"description": "Not found"}},
)
router.add_api_route(
    "",
    refresh_cache,
    methods=["GET"],
)