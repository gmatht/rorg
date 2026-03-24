"""Parity between Rust `buzhash` primitives and Cython `borg.chunkers.buzhash`."""

import os

import pytest

from .. import rust_bridge

pytest.importorskip("borg.chunkers.buzhash", reason="chunkers Cython extension required")

from borg.chunkers.buzhash import buzhash as cy_buzhash
from borg.chunkers.buzhash import buzhash_update as cy_buzhash_update


def _rust():
    if rust_bridge.rust_buzhash(b"x", 0) is None:
        pytest.skip("borg_rust_ext not available")


def test_buzhash_matches_cython_random():
    _rust()
    for _ in range(200):
        n = max(1, int.from_bytes(os.urandom(2), "little") % 8193)
        data = os.urandom(n)
        seed = int.from_bytes(os.urandom(4), "little", signed=True) & 0xFFFFFFFF
        r = rust_bridge.rust_buzhash(data, seed)
        c = cy_buzhash(data, seed)
        assert r == c, (n, seed)


def test_buzhash_update_matches_cython():
    _rust()
    for _ in range(200):
        seed = int.from_bytes(os.urandom(4), "little", signed=True) & 0xFFFFFFFF
        remove = os.urandom(1)[0]
        add = os.urandom(1)[0]
        length = int.from_bytes(os.urandom(2), "little") % 5000 + 1
        sum_ = int.from_bytes(os.urandom(4), "little") & 0xFFFFFFFF
        r = rust_bridge.rust_buzhash_update(sum_, remove, add, length, seed)
        c = cy_buzhash_update(sum_, remove, add, length, seed)
        assert r == c, (seed, length, sum_)
