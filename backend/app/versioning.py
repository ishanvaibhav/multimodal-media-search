"""Explicit indexing/embedding version constants.

Any change to these constants that affects vector compatibility or indexed
frame semantics MUST be treated as a breaking index change:

* ``PREPROCESSING_VERSION`` — image/text preprocessing (SigLIP processor config).
* ``EMBEDDING_VERSION``     — the embedding model used (name + revision are
  tracked separately in the model registry).
* ``SAMPLING_VERSION``      — coarse/fine frame sampling strategy.
* ``DEDUP_VERSION``         — perceptual deduplication strategy/hash.
* ``INDEXING_VERSION``      — overall index layout (what metadata/rows/vectors
  an index contains, e.g. which frame types are persisted).
* ``FINE_EXTRACTION_VERSION`` — fine-search cache artifact layout.

Bumping ``INDEXING_VERSION`` or ``FINE_EXTRACTION_VERSION`` invalidates cached
fine-search artifacts; bumping ``EMBEDDING_VERSION``/``PREPROCESSING_VERSION``
(alongside a model change) invalidates vectors. The startup model-compatibility
check and the fine-search cache manifest both consult these constants.
"""
from __future__ import annotations

SAMPLING_VERSION = "1"
DEDUP_VERSION = "1"
PREPROCESSING_VERSION = "siglip-1"
EMBEDDING_VERSION = "1"
INDEXING_VERSION = "1"
FINE_EXTRACTION_VERSION = "1"

# Fine-search cache manifest signature: bump when the cache layout changes.
FINE_CACHE_SIGNATURE = "fine-v1"
