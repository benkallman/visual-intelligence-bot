from __future__ import annotations
import logging
from .config import get_config
from .interface import LLMRequest, LLMResponse, BaseProvider
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider
from .xai_provider import XAIProvider
from .ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type[BaseProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "xai": XAIProvider,
    "ollama": OllamaProvider,
}


class ProviderUnavailableError(RuntimeError):
    pass


def complete(request: LLMRequest) -> LLMResponse:
    cfg = get_config()
    errors: list[str] = []

    for name in cfg.fallback_order:
        provider_cls = _REGISTRY.get(name)
        if provider_cls is None:
            logger.warning("Unknown provider '%s' in PROVIDER_FALLBACK_ORDER — skipping", name)
            continue

        provider = provider_cls()

        if not provider.is_available():
            logger.info("Provider '%s' skipped: credentials or service not available", name)
            continue

        try:
            logger.info("Calling provider: %s", name)
            return provider.complete(request)
        except Exception as exc:
            logger.warning("Provider '%s' failed: %s", name, exc)
            errors.append(f"{name}: {exc}")

    lines = "\n  ".join(errors) if errors else "(none attempted successfully)"
    raise ProviderUnavailableError(
        "All providers in PROVIDER_FALLBACK_ORDER failed or were unavailable.\n"
        f"  {lines}\n"
        "Fix: set at least one provider API key, or start Ollama locally."
    )
