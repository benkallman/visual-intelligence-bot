from __future__ import annotations
import anthropic
from .interface import BaseProvider, LLMRequest, LLMResponse
from .config import get_config


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def is_available(self) -> bool:
        return bool(get_config().anthropic_api_key)

    def complete(self, request: LLMRequest) -> LLMResponse:
        cfg = get_config()
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        model = cfg.anthropic_model

        if request.image_url:
            content: list | str = [
                {"type": "image", "source": {"type": "url", "url": request.image_url}},
                {"type": "text", "text": request.user_text},
            ]
        else:
            content = request.user_text

        message = client.messages.create(
            model=model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": content}],
        )
        return LLMResponse(
            text=message.content[0].text,
            provider_used=self.name,
            model_used=model,
        )
