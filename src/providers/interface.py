from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class LLMRequest:
    system: str
    user_text: str
    image_url: str | None = None
    max_tokens: int = 2048


@dataclass
class LLMResponse:
    text: str
    provider_used: str
    model_used: str


class BaseProvider:
    name: str = ""

    def is_available(self) -> bool:
        raise NotImplementedError

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
