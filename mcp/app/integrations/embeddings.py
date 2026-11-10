"""Image and text embeddings with SigLIP 2, made on the GPU only."""

from __future__ import annotations

import io
import logging
import threading
import time

import numpy as np
from PIL import Image

from app.core.config import AppConfig
from app.integrations.gpu_worker import IdleWorker, WorkerDiedError
from app.integrations.model_usage import ModelUsage

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
        self.usage = ModelUsage(model_name, "Image and text embeddings for meaning search", self._state)

    def _state(self) -> tuple[bool, str]:
        return (True, "cuda") if self._model is not None else (False, "loads on first use (CUDA only)")

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
        with self.usage.record():
            return self._attempts(model, method, inputs)

    def _attempts(self, model, method: str, inputs) -> np.ndarray:
        import torch

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
        inputs = processor(text=[text], padding="max_length", max_length=AppConfig.EMBEDDING_TEXT_TOKENS,
                           truncation=True, return_tensors="pt")
        return self._unit(self._run("get_text_features", inputs))[0].round(6).tolist()


class IdleSiglipEmbedder:
    """``SiglipEmbedder`` in its own process, which exits after ``idle_seconds`` unused.

    A batch of uploads loads SigLIP 2 once and embeds every image; a few minutes after
    the last one the process ends and the GPU is fully free again (see gpu_worker.py).
    A process that cannot start or dies counts as no GPU: the image's embedding waits
    for ``backfill_metadata``, as with any other ``EmbeddingUnavailableError``.
    """

    def __init__(self, model_name: str = AppConfig.EMBEDDING_MODEL,
                 idle_seconds: float = AppConfig.GPU_MODEL_IDLE_SECONDS) -> None:
        self.model_name = model_name
        self.worker = IdleWorker(SiglipEmbedder, (model_name,), idle_seconds=idle_seconds)
        self.usage = ModelUsage(model_name, "Image and text embeddings for meaning search", self._state)

    def _state(self) -> tuple[bool, str]:
        if self.worker.alive:
            return True, "cuda (own process)"
        return False, f"loads on first use (CUDA only); unloads after {self.worker.idle_seconds:.0f} s idle"

    def _call(self, method: str, value):
        with self.usage.record():
            try:
                return self.worker.call(method, value)
            except WorkerDiedError as error:
                raise EmbeddingUnavailableError(str(error)) from error

    def embed_image(self, data: bytes) -> list[float]:
        return self._call("embed_image", data)

    def embed_text(self, text: str) -> list[float]:
        return self._call("embed_text", text)
