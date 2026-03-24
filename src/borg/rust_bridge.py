"""
Optional Rust pipeline bridge.

This module keeps Borg functional without Rust extensions by providing
best-effort import and explicit fallback behavior.
"""

from __future__ import annotations

import os
from typing import Optional

BufferLike = bytes | memoryview

_EXT = None
_EXT_IMPORT_ERROR = None


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


def rust_enabled() -> bool:
    return _as_bool(os.environ.get("BORG_RUST_PIPELINE"))


def rust_combined_enabled() -> bool:
    return _as_bool(os.environ.get("BORG_RUST_PIPELINE_COMBINED"))


def rust_compress_enabled() -> bool:
    return _as_bool(os.environ.get("BORG_RUST_COMPRESS"))


def rust_encrypt_enabled() -> bool:
    return _as_bool(os.environ.get("BORG_RUST_ENCRYPT"))


def rust_chunker_enabled() -> bool:
    """When true, ``get_chunker(..., algo='buzhash')`` may use the Rust streaming chunker (including sparse files)."""
    return _as_bool(os.environ.get("BORG_RUST_CHUNKER"))


def get_rust_ext():
    """Return the loaded ``borg_rust_ext`` module, or ``None`` if missing or failed to import."""
    return _load_ext()


def _load_ext():
    global _EXT, _EXT_IMPORT_ERROR
    if _EXT is not None:
        return _EXT
    try:
        from . import borg_rust_ext  # type: ignore[attr-defined]
    except Exception as exc:
        _EXT_IMPORT_ERROR = exc
        return None
    _EXT_IMPORT_ERROR = None
    _EXT = borg_rust_ext
    return _EXT


def get_rust_ext_status(required_symbols: tuple[str, ...] = ()) -> tuple[str, str]:
    """
    Return extension status as ``(state, details)``.

    States:
      - loaded: extension imported and all required symbols found
      - missing_symbol: extension imported but one or more symbols are missing
      - import_error: extension import failed
    """
    ext = _load_ext()
    if ext is None:
        if _EXT_IMPORT_ERROR is None:
            return "import_error", "import of borg.borg_rust_ext failed (unknown reason)"
        return "import_error", f"import of borg.borg_rust_ext failed: {_EXT_IMPORT_ERROR!r}"
    missing = [symbol for symbol in required_symbols if not hasattr(ext, symbol)]
    if missing:
        return "missing_symbol", f"extension missing required symbol(s): {', '.join(missing)}"
    return "loaded", "extension loaded"


def compress(meta: dict, data: BufferLike, *, jobs: Optional[int] = None, ctype: int, clevel: int):
    ext = _load_ext()
    if ext is None or not hasattr(ext, "compress"):
        return None
    return ext.compress(meta, data, ctype, clevel, jobs=jobs)


def encrypt(id_bytes: bytes, data: BufferLike, *, jobs: Optional[int] = None):
    ext = _load_ext()
    if ext is None or not hasattr(ext, "encrypt"):
        return None
    try:
        return ext.encrypt(id_bytes, data, jobs=jobs)
    except NotImplementedError:
        return None


def compress_encrypt(
    id_bytes: bytes, meta: dict, data: BufferLike, *, jobs: Optional[int] = None, ctype: int, clevel: int
):
    ext = _load_ext()
    if ext is None or not hasattr(ext, "compress_encrypt"):
        return None
    try:
        return ext.compress_encrypt(id_bytes, meta, data, ctype, clevel, jobs=jobs)
    except NotImplementedError:
        return None


def benchmark_pool_setup(*, iterations: int = 20, jobs: Optional[int] = None):
    """Microbenchmark Rayon pool construction; returns list of (wall_ms, cpu_ms) per iteration."""
    ext = _load_ext()
    if ext is None or not hasattr(ext, "benchmark_pool_setup"):
        return None
    return ext.benchmark_pool_setup(iterations=iterations, jobs=jobs)


def pipeline_timing_get():
    """Return last parallel-copy timing (pool_acquire_ms, parallel_copy_ms, total_ms), or zeros if none."""
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_timing_get"):
        return None
    return ext.pipeline_timing_get()


def pipeline_timing_reset() -> None:
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_timing_reset"):
        return
    ext.pipeline_timing_reset()


def pipeline_parallel_stats_get():
    """Return (pool_ms, copy_ms, total_ms, rayon_pairs, input_len, thread_count) or None if unavailable."""
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_parallel_stats_get"):
        return None
    return ext.pipeline_parallel_stats_get()


def pipeline_concurrency_stats_get():
    """Return (active_ms, avg_in_flight, max_in_flight) or None if unavailable."""
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_concurrency_stats_get"):
        return None
    return ext.pipeline_concurrency_stats_get()


def pipeline_concurrency_stats_reset() -> None:
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_concurrency_stats_reset"):
        return
    ext.pipeline_concurrency_stats_reset()


def pipeline_concurrency_inflight_update(in_flight: int) -> None:
    ext = _load_ext()
    if ext is None or not hasattr(ext, "pipeline_concurrency_inflight_update"):
        return
    ext.pipeline_concurrency_inflight_update(in_flight)


def rust_buzhash(data: bytes, seed: int) -> int | None:
    """Rolling buzhash over `data`; must match `borg.chunkers.buzhash.buzhash` for the same inputs."""
    ext = _load_ext()
    if ext is None or not hasattr(ext, "buzhash"):
        return None
    return ext.buzhash(data, seed & 0xFFFFFFFF)


def rust_buzhash_update(sum_: int, remove: int, add: int, length: int, seed: int) -> int | None:
    ext = _load_ext()
    if ext is None or not hasattr(ext, "buzhash_update"):
        return None
    return ext.buzhash_update(sum_, remove & 0xFF, add & 0xFF, length, seed & 0xFFFFFFFF)


def buzhash_chunk_bytes(
    data: bytes, seed: int, chunk_min_exp: int, chunk_max_exp: int, hash_mask_bits: int, hash_window_size: int
) -> list[bytes] | None:
    """
    Content-defined chunks matching Cython ``Chunker`` on dense input (same as ``BytesIO``).

    Returns ``None`` if the Rust extension is missing. For ``borg create`` chunking, set
    ``BORG_RUST_CHUNKER=1`` to use the Rust streaming chunker via ``get_chunker`` (see
    ``docs/misc/rust_pipeline.rst``). In-memory chunking is dense; sparse files use ``feed_zeros`` in the extension.
    """
    ext = _load_ext()
    if ext is None or not hasattr(ext, "buzhash_chunk_bytes"):
        return None
    return ext.buzhash_chunk_bytes(data, seed, chunk_min_exp, chunk_max_exp, hash_mask_bits, hash_window_size)
