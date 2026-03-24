"""
Optional Rust-backed buzhash chunker (dense and sparse streams).

Used when ``BORG_RUST_CHUNKER=1`` and ``borg_rust_ext`` is built; see :doc:`/misc/rust_pipeline`.
"""

from __future__ import annotations

import time
from typing import Any

from .. import rust_bridge
from ..constants import CH_ALLOC, CH_DATA, zeros
from .buzhash import Chunker as CythonChunker
from .reader import Chunk, FileReader


def rust_chunker_available() -> bool:
    """Return True if the extension exposes ``RustBuzhashChunker``."""
    ext = rust_bridge.get_rust_ext()
    return ext is not None and hasattr(ext, "RustBuzhashChunker")


class RustChunker:
    """
    Drop-in for Cython ``Chunker``: same chunk boundaries as ``buzhash.Chunker``.

    Sparse files (``SEEK_HOLE`` / ``SEEK_DATA``) are supported via ``feed_zeros`` in the extension.
    A non-default ``fmap`` still falls back to Cython; use Cython when the extension is missing.
    """

    __slots__ = (
        "seed",
        "chunk_min_exp",
        "chunk_max_exp",
        "hash_mask_bits",
        "hash_window_size",
        "sparse",
        "reader_block_size",
        "chunking_time",
        "_fd",
        "fh",
        "_file_reader",
        "_rust",
        "_file_eof",
    )

    def __init__(
        self,
        seed: int,
        chunk_min_exp: int,
        chunk_max_exp: int,
        hash_mask_bits: int,
        hash_window_size: int,
        *,
        sparse: bool = False,
    ):
        self.seed = seed
        self.chunk_min_exp = chunk_min_exp
        self.chunk_max_exp = chunk_max_exp
        self.hash_mask_bits = hash_mask_bits
        self.hash_window_size = hash_window_size
        self.sparse = sparse
        self.reader_block_size = 1024 * 1024
        self.chunking_time = 0.0
        self._fd = None
        self.fh = -1
        self._file_reader = None
        self._rust = None
        self._file_eof = False

    def _new_rust(self) -> Any:
        ext = rust_bridge.get_rust_ext()
        if ext is None:
            raise RuntimeError("borg_rust_ext is not available")
        return ext.RustBuzhashChunker(
            self.seed, self.chunk_min_exp, self.chunk_max_exp, self.hash_mask_bits, self.hash_window_size
        )

    def chunkify(self, fd, fh=-1, fmap=None):
        """
        Same signature as Cython ``Chunker.chunkify``.

        Falls back to Cython when ``fmap`` is set (custom file maps).
        """
        if fmap is not None:
            return CythonChunker(
                self.seed,
                self.chunk_min_exp,
                self.chunk_max_exp,
                self.hash_mask_bits,
                self.hash_window_size,
                sparse=self.sparse,
            ).chunkify(fd, fh, fmap)

        self._fd = fd
        self.fh = fh
        self._file_reader = FileReader(fd=fd, fh=fh, read_size=self.reader_block_size, sparse=self.sparse, fmap=None)
        self._rust = self._new_rust()
        self._file_eof = False
        return self

    def __iter__(self):
        return self

    def __next__(self):
        if self._rust is None or self._file_reader is None:
            raise RuntimeError("RustChunker.chunkify not called")

        started_chunking = time.monotonic()
        while True:
            if self._rust.is_finished():
                self.chunking_time += time.monotonic() - started_chunking
                raise StopIteration

            py_chunk = self._rust.next_chunk()
            if py_chunk is not None:
                data = py_chunk if isinstance(py_chunk, (bytes, memoryview)) else bytes(py_chunk)
                got = len(data)
                if zeros.startswith(data):
                    data = None
                    allocation = CH_ALLOC
                else:
                    allocation = CH_DATA
                self.chunking_time += time.monotonic() - started_chunking
                return Chunk(data, size=got, allocation=allocation)

            if self._file_eof:
                self._rust.set_eof()
                continue

            block = self._file_reader.read(self.reader_block_size)
            n = block.meta["size"]
            if n == 0:
                self._file_eof = True
                self._rust.set_eof()
                continue

            alloc = block.meta["allocation"]
            if alloc == CH_DATA:
                raw = block.data
                if raw is None:
                    self._rust.feed_zeros(n)
                else:
                    self._rust.feed(raw)
            else:
                self._rust.feed_zeros(n)
