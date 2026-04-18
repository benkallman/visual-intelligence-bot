from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_ORDER = "anthropic,openai,xai,ollama"

_REMOTE_PROVIDERS = {"anthropic", "openai", "xai"}


class ProviderConfig:
    def __init__(self) -> None:
        raw = os.getenv("PROVIDER_FALLBACK_ORDER", _DEFAULT_ORDER)
        self.fallback_order: list[str] = [p.strip() for p in raw.split(",") if p.strip()]

        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o")

        self.xai_api_key = os.getenv("XAI_API_KEY", "")
        self.xai_model = os.getenv("XAI_MODEL", "grok-2-vision-1212")

        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llava")


_instance: ProviderConfig | None = None


def get_config() -> ProviderConfig:
    global _instance
    if _instance is None:
        _instance = ProviderConfig()
    return _instance


def validate_providers() -> list[str]:
    """Check which providers have usable configuration.

    Logs the status of every provider in the fallback order.
    Raises RuntimeError if no provider can possibly work.
    Returns the list of providers that appear usable (credentials present or Ollama reachable).
    """
    cfg = get_config()
    usable: list[str] = []
    skipped: list[str] = []

    key_map = {
        "anthropic": cfg.anthropic_api_key,
        "openai": cfg.openai_api_key,
        "xai": cfg.xai_api_key,
    }

    for name in cfg.fallback_order:
        if name in _REMOTE_PROVIDERS:
            if key_map.get(name):
                logger.info("Provider '%s': credentials present", name)
                usable.append(name)
            else:
                logger.warning(
                    "Provider '%s': no API key — skipped. "
                    "Set %s_API_KEY to enable.",
                    name,
                    name.upper(),
                )
                skipped.append(name)
        elif name == "ollama":
            # Defer actual reachability to call time; treat presence in order as usable.
            logger.info(
                "Provider 'ollama': local fallback enabled (base URL: %s, model: %s). "
                "Reachability checked at call time.",
                cfg.ollama_base_url,
                cfg.ollama_model,
            )
            usable.append("ollama")
        else:
            logger.warning("Provider '%s': unknown — skipped", name)
            skipped.append(name)

    if not usable:
        raise RuntimeError(
            "No usable providers found.\n"
            f"  Fallback order: {cfg.fallback_order}\n"
            f"  Skipped: {skipped}\n"
            "Fix: set at least one of ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, "
            "or add 'ollama' to PROVIDER_FALLBACK_ORDER and start Ollama locally."
        )

    return usable
