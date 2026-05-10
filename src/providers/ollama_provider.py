from __future__ import annotations
import base64
import logging
import os
import requests
import time
from .interface import BaseProvider, LLMRequest, LLMResponse
from .config import get_config

# Timeouts are read once at import time so they appear in logs from the start.
# Override via environment variables before starting the process.
_TIMEOUT_IMAGE = int(os.environ.get("OLLAMA_IMAGE_TIMEOUT", "600"))  # seconds
_TIMEOUT_TEXT = int(os.environ.get("OLLAMA_TEXT_TIMEOUT", "300"))    # seconds

_IMAGE_HEADERS = {
    "User-Agent": "visual-intelligence-bot/0.1 (+https://github.com/benkallman/visual-intelligence-bot)"
}

logger = logging.getLogger(__name__)


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

        request_type = "image" if request.image_url or request.image_path else "text"
        image_transport: str | None = None

        if request.image_path:
            image_path = request.image_path
            if not os.path.isabs(image_path):
                image_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..", "..", image_path)
                )
            with open(image_path, "rb") as f:
                raw_bytes = f.read()
            logger.debug(
                "Image load: path=%s bytes=%d model=%s request_type=%s",
                image_path, len(raw_bytes), model, request_type,
            )
            user_message["images"] = [base64.b64encode(raw_bytes).decode()]
            image_transport = "local_file->base64"
        elif request.image_url:
            img_resp = requests.get(request.image_url, headers=_IMAGE_HEADERS, timeout=30)
            content_type = img_resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                body_excerpt = img_resp.text[:200].strip()
                raise ValueError(
                    f"Expected an image URL but received non-image content "
                    f"(Content-Type: {content_type!r}) from {request.image_url!r}. "
                    f"Provide a direct image URL, not a page URL. "
                    f"Response excerpt: {body_excerpt!r}"
                )
            raw_bytes = img_resp.content
            logger.debug(
                "Image download fallback: url=%s content_type=%s bytes=%d model=%s request_type=%s",
                request.image_url, content_type, len(raw_bytes), model, request_type,
            )
            user_message["images"] = [base64.b64encode(raw_bytes).decode()]
            image_transport = "url->base64"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                user_message,
            ],
            "stream": False,
        }

        if request.want_json:
            payload["format"] = "json"
            payload["options"] = {"temperature": 0}

        timeout = _TIMEOUT_IMAGE if request.image_url else _TIMEOUT_TEXT

        # Log request shape without raw image data
        debug_shape = {
            "model": model,
            "request_type": request_type,
            "image_transport": image_transport,
            "image_url": request.image_url,
            "image_count": len(user_message.get("images", [])),
            "want_json": request.want_json,
            "system_chars": len(request.system or ""),
            "user_text_chars": len(request.user_text or ""),
            "timeout_seconds": timeout,
        }
        logger.debug("Ollama request shape: %s", debug_shape)

        started_at = time.perf_counter()
        r = requests.post(
            f"{cfg.ollama_base_url}/api/chat",
            json=payload,
            timeout=timeout,
        )
        inference_seconds = time.perf_counter() - started_at
        if request_type == "image":
            print(f"[timing] llava inference: {inference_seconds:.2f} seconds")
        if not r.ok:
            body_excerpt = r.text[:300].strip() if r.text else ""
            error_detail = (
                f"Ollama HTTP {r.status_code} "
                f"[model={model}, request_type={request_type}, "
                f"image_transport={image_transport}, timeout={timeout}s]"
            )
            if body_excerpt:
                error_detail += f" | response: {body_excerpt}"
            logger.error("Ollama failure: %s", error_detail)
            raise RuntimeError(error_detail)
        text = r.json()["message"]["content"]
        return LLMResponse(text=text, provider_used=self.name, model_used=model)
