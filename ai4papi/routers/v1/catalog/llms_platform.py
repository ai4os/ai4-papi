from fastapi import APIRouter
import requests

from ai4papi import schemas
import ai4papi.conf as papiconf
from .common import Catalog


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


class PlatformLLMsCatalog(Catalog):
    def __init__(self):
        # We keep this for compatibility reasons, but we dont use a repo for the LLMs,
        # we use the LiteLLM proxy to retrieve them.
        super().__init__(repo="", item_type="llm-platform")

    def _get_models_status(self) -> dict:
        """
        Retrieves the latest health checks from LiteLLM.
        """
        response = session.get(f"{LITELLM_URL}/health/latest")
        return response.json().get("latest_health_checks", {})

    def _get_all_items(self):
        """
        Retrieves all the models configured in LiteLLM.
        """
        response = session.get(f"{LITELLM_URL}/models")
        models_data = response.json().get("data", [])

        items = {}
        for model in models_data:
            model_id = model.get("id")
            if model_id:
                items[model_id] = model

        return items

    def get_items(self):
        """
        Retrieves all the models configured in LiteLLM that are healthy.
        """
        response = session.get(f"{LITELLM_URL}/models")
        models_data = response.json().get("data", [])

        health_checks = self._get_models_status()

        status_by_model = {
            s["model_name"]: str(s.get("status", "unknown")).lower()
            for s in health_checks.values()
            if "model_name" in s
        }

        items = {}
        for model in models_data:
            model_id = model.get("id")
            if model_id:
                raw_status = status_by_model.get(model_id, "unknown")

                if raw_status == "healthy":
                    items[model_id] = model

        return items

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
        all_models = self._get_all_items()
        health_checks = self._get_models_status()

        status_by_model = {
            s["model_name"]: str(s.get("status", "unknown")).lower()
            for s in health_checks.values()
            if "model_name" in s
        }

        summary = []
        for m_id, m_info in all_models.items():
            # Remove "all-proxy-models", "default" and "embedding" models
            if (
                m_id == "all-proxy-models"
                or "embedding" in m_id.lower()
                or "default" in m_id.lower()
            ):
                continue

            meta = m_info.copy()
            meta["id"] = m_id

            # Add health status
            raw_status = status_by_model.get(m_id, "unknown")
            if raw_status not in ["healthy", "unhealthy"]:
                raw_status = "unknown"

            meta["status"] = raw_status

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
