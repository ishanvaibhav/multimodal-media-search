"""Embedding services.

A clean, isolated interface backs both the real SigLIP model (via Hugging Face
Transformers, CPU or CUDA) and a lightweight deterministic fallback that keeps
the whole pipeline runnable when torch/transformers or the model weights are
unavailable.

**Semantic-search policy**

* ``embedding_backend=siglip``  -> require the real model (fail fast).
* ``embedding_backend=auto``    -> SigLIP when available; deterministic
  fallback ONLY when ``APP_ENV`` is not ``production``. In production a
  missing model aborts startup — degraded semantic search is never presented
  silently.
* ``embedding_backend=deterministic`` -> explicit, non-semantic baseline
  (plumbing / CI only). ``semantic_search`` is exposed as False everywhere.

Image/text vectors are always L2-normalised so cosine similarity == dot product.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..config import Settings
from ..exceptions import EmbeddingError, ModelUnavailableError
from ..logging_config import get_logger

log = get_logger(__name__)

ImageInput = object  # PIL Image, numpy array, or filesystem path

PREPROCESSING_VERSION = "siglip-1"
INDEXING_VERSION = "1"


class EmbeddingService(ABC):
    name: str = "embedding"
    dim: int = 0
    semantic: bool = False
    model_name: str = ""
    model_version: Optional[str] = None
    preprocessing_version: str = ""

    @abstractmethod
    def embed_text(self, texts: Sequence[str]) -> np.ndarray:  # (N, dim)
        ...

    @abstractmethod
    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:  # (N, dim)
        ...

    def metadata(self) -> dict:
        """Traceability metadata stamped onto every indexed vector."""
        return {
            "embedding_model": self.model_name or self.name,
            "model_version": self.model_version or "",
            "embedding_dim": self.dim,
            "preprocessing_version": self.preprocessing_version,
            "indexing_version": INDEXING_VERSION,
        }


# ---------------------------------------------------------------------------
# SigLIP via Hugging Face Transformers
# ---------------------------------------------------------------------------
class SigLIPEmbeddingService(EmbeddingService):
    name = "siglip"
    semantic = True

    def __init__(
        self,
        model_name: str,
        hf_token: str = "",
        device: str | None = None,
        batch_size: int = 16,
    ):
        self.model_name = model_name
        self.preprocessing_version = PREPROCESSING_VERSION
        self.batch_size = batch_size
        self._lock = threading.Lock()
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise ModelUnavailableError(
                "PyTorch is not installed. Run `pip install -r requirements-ml.txt` "
                "or set EMBEDDING_BACKEND=deterministic."
            ) from exc
        try:
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise ModelUnavailableError(
                "transformers is not installed. Run `pip install -r requirements-ml.txt`."
            ) from exc

        import torch

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        token = hf_token or None
        log.info("Loading SigLIP model %s (device=%s)", model_name, self.device)
        try:
            self.processor = AutoProcessor.from_pretrained(model_name, token=token)
            self.model = AutoModel.from_pretrained(model_name, token=token)
        except Exception as exc:
            raise ModelUnavailableError(
                f"Could not load SigLIP model '{model_name}': {exc}"
            ) from exc
        self.model = self.model.to(self.device).eval()
        self.dim = self._detect_dimension()
        self.model_version = self._detect_version()
        log.info(
            "Model loaded successfully: %s | embedding dimension=%d | device=%s | version=%s",
            model_name, self.dim, self.device, self.model_version or "unknown",
        )

    def _detect_dimension(self) -> int:
        cfg = self.model.config
        if getattr(cfg, "hidden_size", None):
            return int(cfg.hidden_size)
        for sub in ("text_config", "vision_config", "text", "vision"):
            sc = getattr(cfg, sub, None)
            if sc is not None and getattr(sc, "hidden_size", None):
                return int(sc.hidden_size)
        with self.torch.no_grad():
            dummy = self.torch.zeros((1, 3, 224, 224), device=self.device)
            out = self.model.get_image_features(pixel_values=dummy)
        return int(out.pooler_output.shape[-1])

    def _detect_version(self) -> Optional[str]:
        cfg = self.model.config
        for attr in ("_commit_hash", "revision"):
            v = getattr(cfg, attr, None)
            if v:
                return str(v)
        return None

    @staticmethod
    def _normalize(x: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(x, axis=-1, keepdims=True)
        norms[norms == 0] = 1.0
        return (x / norms).astype("float32")

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        texts = [t if t and t.strip() else " " for t in texts]
        with self._lock:
            try:
                inputs = self.processor(
                    text=list(texts), padding="max_length",
                    truncation=True, return_tensors="pt",
                ).to(self.device)
                with self.torch.no_grad():
                    out = self.model.get_text_features(**inputs)
                vec = out.pooler_output
                return self._normalize(vec.detach().cpu().numpy())
            except Exception as exc:
                raise EmbeddingError(f"text embedding failed: {exc}") from exc

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), dtype="float32")
        from PIL import Image

        results: list[np.ndarray] = []
        with self._lock:
            for start in range(0, len(images), self.batch_size):
                batch = images[start : start + self.batch_size]
                pil = [_to_pil(img) for img in batch]
                try:
                    inputs = self.processor(images=pil, return_tensors="pt").to(self.device)
                    with self.torch.no_grad():
                        out = self.model.get_image_features(
                            pixel_values=inputs["pixel_values"]
                        )
                    vec = out.pooler_output
                    results.append(self._normalize(vec.detach().cpu().numpy()))
                except Exception as exc:
                    raise EmbeddingError(f"image embedding failed: {exc}") from exc
        if not results:
            return np.zeros((0, self.dim), dtype="float32")
        return np.concatenate(results, axis=0)


# ---------------------------------------------------------------------------
# Deterministic fallback (no model download) — for plumbing/CI only.
# ---------------------------------------------------------------------------
class DeterministicEmbeddingService(EmbeddingService):
    """Deterministic baseline embeddings (color/shape features for images,
    hashed bag-of-words for text). NOT semantically aligned across modalities;
    intended to exercise the pipeline without a model download."""

    name = "deterministic"
    semantic = False
    dim = 384

    def __init__(self) -> None:
        self.model_name = "deterministic-baseline"
        self.preprocessing_version = "det-1"
        self.dim = 384

    @staticmethod
    def _normalize(x: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(x, axis=-1, keepdims=True)
        norms[norms == 0] = 1.0
        return (x / norms).astype("float32")

    def embed_images(self, images: Sequence[ImageInput]) -> np.ndarray:
        from PIL import Image

        out = np.zeros((len(images), self.dim), dtype="float32")
        for i, img in enumerate(images):
            pil = _to_pil(img)
            feat = _deterministic_image_features(pil)
            out[i, : len(feat)] = feat
        return self._normalize(out)

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        import hashlib

        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            vec = np.zeros(self.dim, dtype="float32")
            words = [w for w in text.lower().split() if w]
            for w in words:
                idx = int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim
                vec[idx] += 1.0
            out[i] = vec
        return self._normalize(out)


def _to_pil(img: ImageInput):
    from PIL import Image

    if isinstance(img, Image.Image):
        return img.convert("RGB")
    if isinstance(img, (str, Path)):
        return Image.open(img).convert("RGB")
    if isinstance(img, np.ndarray):
        return Image.fromarray(img.astype("uint8")).convert("RGB")
    raise EmbeddingError(f"unsupported image input type: {type(img)!r}")


def _deterministic_image_features(pil) -> np.ndarray:
    """Grayscale downsample + colour/gradient histograms -> 320-dim vector."""
    import numpy as np
    from PIL import Image

    small = pil.resize((32, 32), Image.BILINEAR).convert("RGB")
    arr = np.asarray(small, dtype=np.float32) / 255.0

    gray = arr.mean(axis=2)
    down = np.asarray(
        Image.fromarray((gray * 255).astype("uint8")).resize((16, 16), Image.BILINEAR),
        dtype=np.float32,
    ) / 255.0
    gray_feat = down.reshape(-1)  # 256

    hist = []
    for c in range(3):
        h, _ = np.histogram(arr[:, :, c], bins=16, range=(0, 1))
        hist.append(h.astype(np.float32) / (32 * 32))
    color_feat = np.concatenate(hist)  # 48

    grad_x = np.abs(np.diff(gray, axis=1))[:, :-1]
    grad_y = np.abs(np.diff(gray, axis=0))[:-1, :]
    mag = (grad_x[:30, :] + grad_y[:, :30]).reshape(-1)
    ghist, _ = np.histogram(mag, bins=16, range=(0, 1.5))
    grad_feat = ghist.astype(np.float32) / max(1, mag.size)  # 16

    return np.concatenate([gray_feat, color_feat, grad_feat])  # 320


# ---------------------------------------------------------------------------
# Resource detection / batch auto-tuning
# ---------------------------------------------------------------------------
def detect_resources() -> dict:
    import os

    cpu = os.cpu_count() or 1
    ram = 0
    try:
        import shutil
        ram = shutil.disk_usage("/").total  # not RAM; replaced below when possible
    except OSError:
        pass
    try:
        import psutil  # optional
    except ImportError:
        psutil = None
    if psutil is not None:
        ram = psutil.virtual_memory().total

    gpu = False
    vram = 0
    try:
        import torch
        if torch.cuda.is_available():
            gpu = True
            vram = int(torch.cuda.get_device_properties(0).total_memory)
    except Exception:
        pass
    return {"cpu": cpu, "ram": ram, "gpu": gpu, "vram": vram}


def suggest_batch_size(settings: Settings) -> int:
    """Pick a safe image-embedding batch size for the available hardware."""
    if settings.embedding_batch_size and settings.embedding_batch_size > 0:
        return settings.embedding_batch_size
    res = detect_resources()
    if res["gpu"]:
        return 64
    # CPU: bounded by core count; images at 224px use ~1-2 GB/1000 in torch
    return max(4, min(16, (res["cpu"] // 2)))


# ---------------------------------------------------------------------------
def create_embedding_service(settings: Settings) -> EmbeddingService:
    backend = settings.embedding_backend.strip().lower()

    if backend == "deterministic":
        svc: EmbeddingService = DeterministicEmbeddingService()
        log.info(
            "Embedding backend: deterministic fallback (dim=%d, semantic_search=%s)",
            svc.dim, svc.semantic,
        )
        return svc

    if backend in ("siglip", "auto"):
        try:
            svc = SigLIPEmbeddingService(
                model_name=settings.siglip_model,
                hf_token=settings.hf_token,
                batch_size=suggest_batch_size(settings),
            )
            log.info(
                "Embedding backend: SigLIP (dim=%d, semantic_search=%s)",
                svc.dim, svc.semantic,
            )
            return svc
        except ModelUnavailableError as exc:
            if backend == "siglip" or settings.production:
                if settings.production:
                    raise ModelUnavailableError(
                        f"Semantic embedding model is REQUIRED in production "
                        f"(APP_ENV=production) but could not be loaded: {exc.message}"
                    ) from exc
                raise
            log.warning(
                "SigLIP unavailable (%s); falling back to deterministic embeddings. "
                "Semantic search is DISABLED. Set EMBEDDING_BACKEND=siglip to fail "
                "fast instead.",
                exc.message,
            )
            svc = DeterministicEmbeddingService()
            return svc

    raise ModelUnavailableError(
        f"Unknown EMBEDDING_BACKEND '{settings.embedding_backend}'. "
        "Use 'auto', 'siglip' or 'deterministic'."
    )
