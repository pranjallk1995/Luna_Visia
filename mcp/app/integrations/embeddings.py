"""Image and text embeddings with SigLIP 2, made on the GPU only."""

from __future__ import annotations

import io
import logging
import threading
import time

import numpy as np
from PIL import Image

from app.core.config import AppConfig

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when no embedding can be made: there is no CUDA GPU, or it stayed out of memory."""


class SiglipEmbedder:
    """Embed images (and text) into SigLIP 2's shared space, on a CUDA GPU in half precision.

    Embeddings are never made on the CPU. The model loads on the first call and
    stays loaded; without a CUDA device every call raises
    ``EmbeddingUnavailableError``. If the GPU runs out of memory, a call is
    retried a few times before giving up, so a busy GPU only delays an image's
    embedding: ``backfill_metadata`` adds missing ones later.
    """

    def __init__(self, model_name: str = AppConfig.EMBEDDING_MODEL) -> None:
        self.model_name = model_name
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                import torch
                from transformers import AutoModel, AutoProcessor

                if not torch.cuda.is_available():
                    raise EmbeddingUnavailableError(
                        "Image embeddings need a CUDA GPU, and none is available to Luna Visia."
                    )
                self._processor = AutoProcessor.from_pretrained(self.model_name)
                self._model = (
                    AutoModel.from_pretrained(self.model_name, dtype=torch.float16).eval().to("cuda")
                )
            return self._model, self._processor

    def _run(self, method: str, inputs) -> np.ndarray:
        import torch

        model, _ = self._load()
        for attempt in range(AppConfig.EMBEDDING_GPU_ATTEMPTS):
            try:
                with torch.no_grad():
                    return self._features(getattr(model, method)(**inputs.to("cuda")))
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if attempt + 1 < AppConfig.EMBEDDING_GPU_ATTEMPTS:
                    time.sleep(AppConfig.EMBEDDING_GPU_RETRY_SECONDS * (attempt + 1))
        raise EmbeddingUnavailableError("The GPU stayed out of memory; no embedding was made.")

    @staticmethod
    def _features(output) -> np.ndarray:
        """The projected embeddings, whether a call returns a tensor or (Transformers 5) an output object."""
        tensor = output if hasattr(output, "numpy") else output.pooler_output
        return tensor.detach().float().cpu().numpy()

    @staticmethod
    def _unit(vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)

    def embed_image(self, data: bytes) -> list[float]:
        """One unit-length embedding for an image's bytes."""
        _, processor = self._load()
        with Image.open(io.BytesIO(data)) as image:
            image.draft("RGB", (AppConfig.EMBEDDING_INPUT_EDGE * 2,) * 2)
            pixels = image.convert("RGB")
        inputs = processor(images=[pixels], return_tensors="pt").to(dtype=self._model.dtype)
        return self._unit(self._run("get_image_features", inputs))[0].round(6).tolist()

    def embed_text(self, text: str) -> list[float]:
        """A unit-length embedding of a description, comparable with image embeddings."""
        _, processor = self._load()
        inputs = processor(text=[text], padding="max_length", max_length=64, return_tensors="pt")
        return self._unit(self._run("get_text_features", inputs))[0].round(6).tolist()
