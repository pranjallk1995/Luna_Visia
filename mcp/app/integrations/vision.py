"""Local Ollama image analysis."""

import base64
from io import BytesIO

import httpx
from PIL import Image, ImageOps

from app.core.config import AppConfig
from app.core.models import ImageAnalysis, MetadataRules


class OllamaVisionAnalyzer:
    """Generate searchable image metadata with a local Ollama vision model."""

    PROMPT = (
        "Analyze only the supplied image. Return a concise factual caption "
        "and 5-12 lowercase searchable tags covering subjects, objects, "
        "setting, colors, and style, in English. Do not infer from a filename."
    )

    def __init__(self, base_url: str, model: str, rules: MetadataRules) -> None:
        self.base_url = base_url
        self.model = model
        self.rules = rules

    @classmethod
    def prepare_image(cls, image_data: bytes) -> bytes:
        """Bound vision input cost without changing the stored original."""
        with Image.open(BytesIO(image_data)) as source:
            frame = ImageOps.exif_transpose(source).convert("RGB")
            frame.thumbnail((AppConfig.VISION_MAX_INPUT_EDGE, AppConfig.VISION_MAX_INPUT_EDGE))
            output = BytesIO()
            frame.save(output, format="JPEG", quality=88, optimize=True)
        return output.getvalue()

    def analyze(self, image_data: bytes) -> ImageAnalysis:
        """Analyze image bytes and return schema-validated metadata."""
        payload = {
            "model": self.model,
            "stream": False,
            "format": ImageAnalysis.model_json_schema(),
            # Thinking models (Qwen3-VL) otherwise reason at length before every reply.
            "think": False,
            "options": {"temperature": 0, "num_ctx": AppConfig.VISION_CONTEXT_TOKENS},
            "messages": [
                {
                    "role": "user",
                    "content": self.PROMPT,
                    "images": [
                        base64.b64encode(self.prepare_image(image_data)).decode("ascii")
                    ],
                }
            ],
        }
        with httpx.Client(timeout=httpx.Timeout(AppConfig.VISION_TIMEOUT_SECONDS, connect=AppConfig.VISION_CONNECT_TIMEOUT_SECONDS)) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
        analysis = ImageAnalysis.model_validate_json(response.json()["message"]["content"])
        analysis.caption = analysis.caption.strip()
        analysis.tags = self.rules.normalize_tags(analysis.tags)
        return analysis
