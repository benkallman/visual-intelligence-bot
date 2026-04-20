from __future__ import annotations
import base64
import io
import logging
import requests
from PIL import Image
from .interface import BaseProvider, LLMRequest, LLMResponse
from .config import get_config

_MAX_SIDE = 768
_JPEG_QUALITY = 75
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

        request_type = "image" if request.image_url else "text"
        image_transport: str | None = None

        if request.image_url:
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
                "Image download: url=%s content_type=%s original_bytes=%d model=%s request_type=%s",
                request.image_url, content_type, len(raw_bytes), model, request_type,
            )
            img = Image.open(io.BytesIO(raw_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > _MAX_SIDE:
                scale = _MAX_SIDE / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            resized_w, resized_h = img.size
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            compressed_bytes = buf.getvalue()
            logger.debug(
                "Image preprocessed: original_bytes=%d resized=%dx%d compressed_bytes=%d "
                "max_side=%d jpeg_quality=%d model=%s",
                len(raw_bytes), resized_w, resized_h, len(compressed_bytes),
                _MAX_SIDE, _JPEG_QUALITY, model,
            )
            user_message["images"] = [base64.b64encode(compressed_bytes).decode()]
            image_transport = "url->resize->jpeg->base64"

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
        }
        logger.debug("Ollama request shape: %s", debug_shape)

        r = requests.post(
            f"{cfg.ollama_base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        if not r.ok:
            body_excerpt = r.text[:300].strip() if r.text else ""
            error_detail = (
                f"Ollama HTTP {r.status_code} "
                f"[model={model}, request_type={request_type}, "
                f"image_transport={image_transport}]"
            )
            if body_excerpt:
                error_detail += f" | response: {body_excerpt}"
            logger.error("Ollama failure: %s", error_detail)
            raise RuntimeError(error_detail)
        text = r.json()["message"]["content"]
        return LLMResponse(text=text, provider_used=self.name, model_used=model)
