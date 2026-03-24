"""Rust ``buzhash_chunk_bytes`` / ``RustChunker`` vs Cython ``Chunker`` on dense and sparse input."""

import os
import tempfile
from io import BytesIO

import pytest

from .. import rust_bridge
from ..chunkers import Chunker, get_chunker, rust_buzhash
from ..constants import CHUNKER_PARAMS, HASH_WINDOW_SIZE
from .chunkers import cf_expand, fs_supports_sparse, make_sparsefile, map_sparse1

pytest.importorskip("borg.chunkers.buzhash", reason="chunkers Cython extension required")


def _need_rust():
    if rust_bridge.buzhash_chunk_bytes(b"x", 0, 19, 23, 21, HASH_WINDOW_SIZE) is None:
        pytest.skip("borg_rust_ext not available")


def _need_rust_streaming():
    if not rust_buzhash.rust_chunker_available():
        pytest.skip("borg_rust_ext RustBuzhashChunker not available")


def test_rust_chunker_matches_cython_default_params():
    _need_rust()
    _, min_exp, max_exp, mask_bits, win = CHUNKER_PARAMS
    data = os.urandom(3 * 1024 * 1024)
    seed = 0x12345678
    bio = BytesIO(data)
    cy = Chunker(seed, min_exp, max_exp, mask_bits, win)
    cy_chunks = cf_expand(cy.chunkify(bio, -1))
    rust_chunks = rust_bridge.buzhash_chunk_bytes(data, seed, min_exp, max_exp, mask_bits, win)
    assert rust_chunks is not None
    assert cy_chunks == rust_chunks
    assert b"".join(cy_chunks) == data


def test_rust_streaming_chunkify_matches_cython_file():
    """``RustChunker`` + ``FileReader`` vs Cython ``Chunker`` on the same on-disk file."""
    _need_rust_streaming()
    _, min_exp, max_exp, mask_bits, win = CHUNKER_PARAMS
    data = os.urandom(2 * 1024 * 1024)
    seed = 0xABCD1234
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data)
        path = f.name
    try:
        with open(path, "rb") as fd:
            cy = Chunker(seed, min_exp, max_exp, mask_bits, win)
            cy_chunks = cf_expand(cy.chunkify(fd, -1))
        with open(path, "rb") as fd:
            rs = rust_buzhash.RustChunker(seed, min_exp, max_exp, mask_bits, win)
            rs_chunks = cf_expand(rs.chunkify(fd, -1))
    finally:
        os.unlink(path)
    assert cy_chunks == rs_chunks
    assert b"".join(cy_chunks) == data


@pytest.mark.skipif(not fs_supports_sparse(), reason="filesystem does not support sparse files")
def test_rust_streaming_chunkify_matches_cython_sparse_file(tmpdir):
    """``RustChunker`` with ``sparse=True`` vs Cython on a real sparse file."""
    _need_rust_streaming()
    _, min_exp, max_exp, mask_bits, win = CHUNKER_PARAMS
    seed = 0x12345678
    fn = str(tmpdir / "sparse1")
    make_sparsefile(fn, map_sparse1)

    cy = Chunker(seed, min_exp, max_exp, mask_bits, win, sparse=True)
    rs = rust_buzhash.RustChunker(seed, min_exp, max_exp, mask_bits, win, sparse=True)

    with open(fn, "rb") as f:
        cy_chunks = cf_expand(list(cy.chunkify(f, -1)))
    with open(fn, "rb") as f:
        rs_chunks = cf_expand(list(rs.chunkify(f, -1)))

    assert cy_chunks == rs_chunks


def test_get_chunker_selects_rust_when_env(monkeypatch):
    _need_rust_streaming()
    monkeypatch.setenv("BORG_RUST_CHUNKER", "1")
    c = get_chunker("buzhash", *CHUNKER_PARAMS[1:], key=None, sparse=False)
    assert isinstance(c, rust_buzhash.RustChunker)
    c_sparse = get_chunker("buzhash", *CHUNKER_PARAMS[1:], key=None, sparse=True)
    assert isinstance(c_sparse, rust_buzhash.RustChunker)
    monkeypatch.delenv("BORG_RUST_CHUNKER", raising=False)
    c2 = get_chunker("buzhash", *CHUNKER_PARAMS[1:], key=None, sparse=False)
    assert isinstance(c2, Chunker)


def test_rust_chunker_matches_cython_smaller_buffer():
    """Same parameters as ``test_buzhash_chunksize_distribution`` (max buffer 64 KiB)."""
    _need_rust()
    min_exp, max_exp, mask = 10, 16, 14
    data = os.urandom(1048576)
    seed = 0
    bio = BytesIO(data)
    cy = Chunker(seed, min_exp, max_exp, mask, 4095)
    cy_chunks = cf_expand(cy.chunkify(bio, -1))
    rust_chunks = rust_bridge.buzhash_chunk_bytes(data, seed, min_exp, max_exp, mask, 4095)
    assert rust_chunks is not None
    assert cy_chunks == rust_chunks
