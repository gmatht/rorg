import pytest

from ..constants import ROBJ_FILE_STREAM, ROBJ_MANIFEST, ROBJ_ARCHIVE_META
from ..crypto.key import PlaintextKey
from ..helpers.errors import IntegrityError
from ..repository import Repository
from ..repoobj import RepoObj, RepoObj1
from ..compress import Compressor, LZ4, LZ4_COMPRESSOR, get_compressor, rust_compress_params
from .. import rust_bridge


@pytest.fixture
def repository(tmpdir):
    return Repository(tmpdir, create=True)


@pytest.fixture
def key(repository):
    return PlaintextKey(repository)


def test_format_parse_roundtrip(key):
    repo_objs = RepoObj(key)
    data = b"foobar" * 10
    id = repo_objs.id_hash(data)
    meta = {"custom": "something"}  # size and csize are computed automatically
    cdata = repo_objs.format(id, meta, data, ro_type=ROBJ_FILE_STREAM)

    got_meta = repo_objs.parse_meta(id, cdata, ro_type=ROBJ_FILE_STREAM)
    assert got_meta["size"] == len(data)
    assert got_meta["csize"] < len(data)
    assert got_meta["custom"] == "something"

    got_meta, got_data = repo_objs.parse(id, cdata, ro_type=ROBJ_FILE_STREAM)
    assert got_meta["size"] == len(data)
    assert got_meta["csize"] < len(data)
    assert got_meta["custom"] == "something"
    assert data == got_data

    edata = repo_objs.extract_crypted_data(cdata)
    key = repo_objs.key
    assert edata.startswith(bytes((key.TYPE,)))


def test_format_parse_roundtrip_borg1(key):  # legacy
    repo_objs = RepoObj1(key)
    data = b"foobar" * 10
    id = repo_objs.id_hash(data)
    meta = {}  # borg1 does not support this kind of metadata
    cdata = repo_objs.format(id, meta, data, ro_type=ROBJ_FILE_STREAM)

    # Borg 1 does not support separate metadata, and Borg 2 does not invoke parse_meta for Borg 1 repositories.

    got_meta, got_data = repo_objs.parse(id, cdata, ro_type=ROBJ_FILE_STREAM)
    assert got_meta["size"] == len(data)
    assert got_meta["csize"] < len(data)
    assert data == got_data

    edata = repo_objs.extract_crypted_data(cdata)
    compressor = repo_objs.compressor
    key = repo_objs.key
    assert edata.startswith(bytes((key.TYPE, compressor.ID, compressor.level)))


def test_borg1_borg2_transition(key):
    # Borg transfer reads Borg 1.x repository objects (without decompressing them),
    # and writes Borg 2 repository objects (providing already-compressed data to avoid recompression).
    meta = {}  # borg1 does not support this kind of metadata
    data = b"foobar" * 10
    len_data = len(data)
    repo_objs1 = RepoObj1(key)
    id = repo_objs1.id_hash(data)
    borg1_cdata = repo_objs1.format(id, meta, data, ro_type=ROBJ_FILE_STREAM)
    meta1, compr_data1 = repo_objs1.parse(
        id, borg1_cdata, decompress=True, want_compressed=True, ro_type=ROBJ_FILE_STREAM
    )  # avoid re-compression
    # In Borg 1, we can only get this metadata after decrypting the whole chunk (and we do not have "size" here):
    assert meta1["ctype"] == LZ4.ID  # Default compression.
    assert meta1["clevel"] == 0xFF  # LZ4 does not support levels (yet?).
    assert meta1["csize"] < len_data  # LZ4 should make it smaller.

    repo_objs2 = RepoObj(key)
    # Note: As we did not decompress, we do not have "size" and need to get it from somewhere else.
    # Here, we just use len_data. For Borg transfer, we also know the size from another metadata source.
    borg2_cdata = repo_objs2.format(
        id,
        dict(meta1),
        compr_data1[2:],
        compress=False,
        size=len_data,
        ctype=meta1["ctype"],
        clevel=meta1["clevel"],
        ro_type=ROBJ_FILE_STREAM,
    )
    meta2, data2 = repo_objs2.parse(id, borg2_cdata, ro_type=ROBJ_FILE_STREAM)
    assert data2 == data
    assert meta2["ctype"] == LZ4.ID
    assert meta2["clevel"] == 0xFF
    assert meta2["csize"] == meta1["csize"] - 2  # Borg 2 does not store the type/level bytes there.
    assert meta2["size"] == len_data

    meta2 = repo_objs2.parse_meta(id, borg2_cdata, ro_type=ROBJ_FILE_STREAM)
    # Now, in Borg 2, we have nice and separately decrypted metadata (no need to decrypt the whole chunk).
    assert meta2["ctype"] == LZ4.ID
    assert meta2["clevel"] == 0xFF
    assert meta2["csize"] == meta1["csize"] - 2  # Borg 2 does not store the type/level bytes there.
    assert meta2["size"] == len_data


def test_spoof_manifest(key):
    repo_objs = RepoObj(key)
    data = b"fake or malicious manifest data"  # File content could be provided by an attacker.
    id = repo_objs.id_hash(data)
    # Create a repository object containing user data (file content data).
    cdata = repo_objs.format(id, {}, data, ro_type=ROBJ_FILE_STREAM)
    # Let's assume an attacker managed to replace the manifest with that repository object.
    # As Borg always gives the ro_type it intends to read, this should fail:
    with pytest.raises(IntegrityError):
        repo_objs.parse(id, cdata, ro_type=ROBJ_MANIFEST)


def test_spoof_archive(key):
    repo_objs = RepoObj(key)
    data = b"fake or malicious archive data"  # File content could be provided by an attacker.
    id = repo_objs.id_hash(data)
    # Create a repository object containing user data (file content data).
    cdata = repo_objs.format(id, {}, data, ro_type=ROBJ_FILE_STREAM)
    # Let's assume an attacker managed to replace an archive with that repository object.
    # As Borg always gives the ro_type it intends to read, this should fail:
    with pytest.raises(IntegrityError):
        repo_objs.parse(id, cdata, ro_type=ROBJ_ARCHIVE_META)


def test_format_combined_rust_path(monkeypatch, key):
    repo_objs = RepoObj(key)
    data = b"combined-path-data" * 1024
    id = repo_objs.id_hash(data)
    meta = {"custom": "combined"}

    monkeypatch.setattr(rust_bridge, "rust_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "rust_combined_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "compress_encrypt", lambda *args, **kwargs: (dict(meta), data, data))

    cdata = repo_objs.format(id, dict(meta), data, ro_type=ROBJ_FILE_STREAM)
    got_meta, got_data = repo_objs.parse(id, cdata, ro_type=ROBJ_FILE_STREAM)
    assert got_meta["custom"] == "combined"
    assert got_data == data


def test_format_combined_rust_passes_memoryview_without_converting(monkeypatch, key):
    """Rust bridge should receive memoryview as-is (no eager bytes() copy on Python side)."""
    repo_objs = RepoObj(key)
    raw = b"mv-test-data" * 500
    id = repo_objs.id_hash(raw)
    meta = {"custom": "mv"}
    captured = None

    def fake_compress_encrypt(id_b, m, data, jobs=None, ctype=None, clevel=None):
        nonlocal captured
        captured = data
        return (dict(m), raw, raw)

    monkeypatch.setattr(rust_bridge, "rust_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "rust_combined_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "compress_encrypt", fake_compress_encrypt)

    mv = memoryview(raw)
    repo_objs.format(id, dict(meta), mv, ro_type=ROBJ_FILE_STREAM)
    assert captured is not None
    assert type(captured) is memoryview


def test_format_combined_rust_passes_bytes(monkeypatch, key):
    repo_objs = RepoObj(key)
    raw = b"bytes-test" * 500
    id = repo_objs.id_hash(raw)
    meta = {"custom": "b"}
    captured = None

    def fake_compress_encrypt(id_b, m, data, jobs=None, ctype=None, clevel=None):
        nonlocal captured
        captured = data
        return (dict(m), raw, raw)

    monkeypatch.setattr(rust_bridge, "rust_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "rust_combined_enabled", lambda: True)
    monkeypatch.setattr(rust_bridge, "compress_encrypt", fake_compress_encrypt)

    repo_objs.format(id, dict(meta), raw, ro_type=ROBJ_FILE_STREAM)
    assert captured is not None
    assert type(captured) is bytes


def test_rust_bridge_pipeline_timing_helpers():
    """Smoke-test: optional extension returns None when unavailable; else tuple of three floats."""
    got = rust_bridge.pipeline_timing_get()
    assert got is None or (isinstance(got, tuple) and len(got) == 3)
    rust_bridge.pipeline_timing_reset()


def test_rust_bridge_parallel_stats_helpers():
    got = rust_bridge.pipeline_parallel_stats_get()
    assert got is None or (isinstance(got, tuple) and len(got) == 6)
    rust_bridge.pipeline_timing_reset()


def test_rust_bridge_concurrency_stats_helpers():
    got = rust_bridge.pipeline_concurrency_stats_get()
    assert got is None or (isinstance(got, tuple) and len(got) == 3)
    rust_bridge.pipeline_concurrency_stats_reset()
    rust_bridge.pipeline_concurrency_inflight_update(0)


def test_rust_bridge_encrypt_not_implemented_falls_back(monkeypatch):
    class _FakeExt:
        def encrypt(self, *_args, **_kwargs):
            raise NotImplementedError

    monkeypatch.setattr(rust_bridge, "_EXT", _FakeExt())
    assert rust_bridge.encrypt(b"id", b"payload") is None


def test_rust_bridge_combined_not_implemented_falls_back(monkeypatch):
    class _FakeExt:
        def compress_encrypt(self, *_args, **_kwargs):
            raise NotImplementedError

    monkeypatch.setattr(rust_bridge, "_EXT", _FakeExt())
    got = rust_bridge.compress_encrypt(b"id", {}, b"payload", jobs=1, ctype=0x01, clevel=255)
    assert got is None


def test_rust_compress_params():
    assert rust_compress_params(LZ4_COMPRESSOR) == (0x01, 255)
    assert rust_compress_params(Compressor(name="zstd", level=3)) == (0x03, 3)
    assert rust_compress_params(Compressor(name="lzma", level=6)) == (0x02, 6)
    assert rust_compress_params(Compressor(name="none")) == (0x00, 255)
    assert rust_compress_params(get_compressor("auto", compressor=get_compressor("lz4"))) is None
    assert rust_compress_params(get_compressor("obfuscate", level=3, compressor=get_compressor("lz4"))) is None


def test_format_rust_compress_roundtrip(monkeypatch, key):
    if rust_bridge.rust_buzhash(b"x", 0) is None:
        pytest.skip("borg_rust_ext not available")
    monkeypatch.setenv("BORG_RUST_PIPELINE", "1")
    monkeypatch.setenv("BORG_RUST_COMPRESS", "1")
    monkeypatch.setenv("BORG_RUST_ENCRYPT", "0")
    repo_objs = RepoObj(key)
    repo_objs.compressor = Compressor(name="zstd", level=3)
    data = b"The quick brown fox jumps over the lazy dog.\n" * 500
    chunk_id = repo_objs.id_hash(data)
    cdata = repo_objs.format(chunk_id, {}, data, ro_type=ROBJ_FILE_STREAM)
    got_meta, got_plain = repo_objs.parse(chunk_id, cdata, ro_type=ROBJ_FILE_STREAM)
    assert got_plain == data
    assert got_meta["ctype"] == 0x03
