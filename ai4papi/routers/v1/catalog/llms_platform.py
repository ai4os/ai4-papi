from cachetools import cached, TTLCache
from fastapi import APIRouter
import requests

from ai4papi import schemas
import ai4papi.conf as papiconf
from ai4papi.routers.v1.catalog.common import Catalog


# LiteLLM API configuration
LITELLM_URL = "https://vllm.cloud.ai4eosc.eu"
LITELLM_API_KEY = papiconf.load_env("LITELLM_API_KEY")


class LiteLLMSession(requests.Session):
    """
    Session that automatically raises a FastAPI HTTPException for failed LiteLLM
    responses.
    """

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        if not response.ok:
            from fastapi import HTTPException

            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response


session = LiteLLMSession()
session.headers.update(
    {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}
)

EXCLUDE_PATTERNS = ["all-proxy-models", "embedding", "default"]


class PlatformLLMsCatalog(Catalog):
    def __init__(self):
        # We keep this for compatibility reasons, but we dont use a repo for the LLMs,
        # we use the LiteLLM proxy to retrieve them.
        super().__init__(repo="", item_type="llm-platform")

    @cached(cache=TTLCache(maxsize=1024, ttl=60))
    def get_items(self):
        """
        Retrieves all the models configured in LiteLLM.
        """
        response = session.get(f"{LITELLM_URL}/models")
        models_data = response.json().get("data", [])

        items = {}
        for model in models_data:
            model_id = model.get("id")
            if model_id and not any(
                pattern in model_id.lower() for pattern in EXCLUDE_PATTERNS
            ):
                items[model_id] = model

        return items

    @cached(cache=TTLCache(maxsize=1024, ttl=60))
    def get_summary(
        self,
        tags: schemas.TagList = None,
        tags_any: schemas.TagList = None,
        not_tags: schemas.TagList = None,
        not_tags_any: schemas.TagList = None,
    ):
        """
        Returns a summary of the available platform LLMs.
        """
        models = self.get_items()

        response = session.get(f"{LITELLM_URL}/health/latest")
        health_checks = response.json().get("latest_health_checks", {})

        status_by_model = {
            s["model_name"]: status
            if (status := str(s.get("status", "")).lower()) in ["healthy", "unhealthy"]
            else "unknown"
            for s in health_checks.values()
            if "model_name" in s
        }

        summary = []
        for m_id, m_info in models.items():
            meta = m_info.copy()
            meta["id"] = m_id
            meta["status"] = status_by_model.get(m_id, "unknown")
            summary.append(meta)

        return summary


PlatformLLMs = PlatformLLMsCatalog()

router = APIRouter(
    prefix="/llms/platform",
    tags=["Catalog (platform wide LLMs)"],
    responses={404: {"description": "Not found"}},
)

router.add_api_route(
    "",
    PlatformLLMs.get_filtered_list,
    methods=["GET"],
)

router.add_api_route(
    "/detail",
    PlatformLLMs.get_summary,
    methods=["GET"],
)
