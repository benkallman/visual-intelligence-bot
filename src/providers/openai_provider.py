from __future__ import annotations
from .interface import BaseProvider, LLMRequest, LLMResponse
from .config import get_config


class OpenAIProvider(BaseProvider):
    name = "openai"

    def is_available(self) -> bool:
        return bool(get_config().openai_api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package not installed — run: pip install openai")

        cfg = get_config()
        client = openai.OpenAI(api_key=cfg.openai_api_key)
        model = cfg.openai_model

        if request.image_url:
            content: list | str = [
                {"type": "image_url", "image_url": {"url": request.image_url}},
                {"type": "text", "text": request.user_text},
            ]
        else:
            content = request.user_text

        response = client.chat.completions.create(
            model=model,
            max_tokens=request.max_tokens,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
        )
        return LLMResponse(
            text=response.choices[0].message.content,
            provider_used=self.name,
            model_used=model,
        )
