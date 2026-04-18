from __future__ import annotations
import base64
import requests
from .interface import BaseProvider, LLMRequest, LLMResponse
from .config import get_config


class OllamaProvider(BaseProvider):
    name = "ollama"

    def is_available(self) -> bool:
        cfg = get_config()
        try:
            r = requests.get(f"{cfg.ollama_base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def complete(self, request: LLMRequest) -> LLMResponse:
        cfg = get_config()
        model = cfg.ollama_model

        user_message: dict = {"role": "user", "content": request.user_text}

        if request.image_url:
            img_bytes = requests.get(request.image_url, timeout=30).content
            user_message["images"] = [base64.b64encode(img_bytes).decode()]

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                user_message,
            ],
            "stream": False,
        }

        r = requests.post(
            f"{cfg.ollama_base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return LLMResponse(text=text, provider_used=self.name, model_used=model)
