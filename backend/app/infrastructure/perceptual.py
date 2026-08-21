"""Perceptual image hashing strategies used by frame deduplication.

Supported methods (selected by ``DEDUP_METHOD``):

* ``phash``      — 64-bit perceptual hash via DCT (robust to brightness shifts).
* ``dhash``      — 64-bit difference hash (fast, gradient based).
* ``embedding``  — cosine similarity of image embeddings (semantic, slower).
* ``none``       — no deduplication.

All hashes are integers; ``similar`` returns True when two frames should be
treated as duplicates given the configured threshold (a hamming *fraction* for
phash/dhash, a cosine similarity for embedding).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..logging_config import get_logger

log = get_logger(__name__)

HASH_BITS = 64


def phash(image_path: Path) -> int:
    """64-bit perceptual hash via DCT (pHASH)."""
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as img:
        gray = img.convert("L").resize((32, 32), Image.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float32)

    # 2-D discrete cosine transform (8x8 low frequencies)
    dct = _dct2(pixels)
    low = dct[:8, :8]
    median = float(np.median(low))
    bits = (low > median).astype(np.uint8).reshape(-1)
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


def _dct2(arr: "np.ndarray") -> "np.ndarray":  # noqa: F821
    import numpy as np

    n, m = arr.shape
    dct = np.zeros((n, m), dtype=np.float64)
    for i in range(n):
        for j in range(m):
            dct[i, j] = np.sum(
                arr * np.cos(np.pi * (2 * np.arange(n)[:, None] + 1) * i / (2 * n))
                * np.cos(np.pi * (2 * np.arange(m)[None, :] + 1) * j / (2 * m))
            )
    return dct


def dhash(image_path: Path) -> int:
    """64-bit difference hash."""
    from PIL import Image

    with Image.open(image_path) as img:
        gray = img.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def hamming_fraction(a: int, b: int) -> float:
    return hamming(a, b) / float(HASH_BITS)


def make_hash_function(method: str) -> Callable[[Path], object]:
    """Return the hash function for a configured dedup method."""
    m = (method or "phash").strip().lower()
    if m == "phash":
        return phash
    if m == "dhash":
        return dhash
    if m in ("none", ""):
        return _identity_hash
    if m == "embedding":
        raise ValueError(
            "the 'embedding' dedup method does not use static hashes; "
            "use the embedding-based comparator instead"
        )
    raise ValueError(f"unknown DEDUP_METHOD '{method}' (phash|dhash|embedding|none)")


def _identity_hash(path: Path) -> int:
    return 0


def similar_hash(a: object, b: object, threshold: float) -> bool:
    """Compare two integer hashes using the hamming-fraction threshold."""
    return hamming_fraction(int(a), int(b)) <= threshold


SUPPORTED_DEDUP_METHODS = ("phash", "dhash", "embedding", "none")


def is_embedding_method(method: str) -> bool:
    return (method or "").strip().lower() == "embedding"


def normalize_method(method: Optional[str]) -> str:
    m = (method or "phash").strip().lower()
    if m not in SUPPORTED_DEDUP_METHODS:
        raise ValueError(f"unknown DEDUP_METHOD '{method}'")
    return m
