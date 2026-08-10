from fastapi import APIRouter

from ai4papi import schemas
import ai4papi.conf as papiconf
from .common import Catalog


class SelfLLMsCatalog(Catalog):
    def __init__(self):
        # We keep this for compatibility reasons, but we dont use a repo for the LLMs,
        # we use the vllm.yaml
        super().__init__(repo="", item_type="llm-self")

    def get_items(self):
        """
        Retrieves all the models configured in the vllm.yaml.
        """
        return papiconf.VLLM.get("models", {})

    def get_summary(
        self,
        tags: schemas.TagList = None,
        tags_any: schemas.TagList = None,
        not_tags: schemas.TagList = None,
        not_tags_any: schemas.TagList = None,
    ):
        """
        Returns a summary of the available self-deployed LLMs.
        """
        models = self.get_items()
        summary = []
        for m_id, m_info in models.items():
            meta = m_info.copy()
            meta["id"] = m_id
            summary.append(meta)
            
        return summary


SelfLLMs = SelfLLMsCatalog()

router = APIRouter(
    prefix="/llms/self",
    tags=["Catalog (self-deployed LLMs)"],
    responses={404: {"description": "Not found"}},
)

router.add_api_route(
    "",
    SelfLLMs.get_filtered_list,
    methods=["GET"],
)

router.add_api_route(
    "/detail",
    SelfLLMs.get_summary,
    methods=["GET"],
)