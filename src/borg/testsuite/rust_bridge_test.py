import types

from .. import rust_bridge


def test_rust_ext_status_import_error(monkeypatch):
    monkeypatch.setattr(rust_bridge, "_EXT", None)
    monkeypatch.setattr(rust_bridge, "_EXT_IMPORT_ERROR", ImportError("test import failure"))
    monkeypatch.setattr(rust_bridge, "_load_ext", lambda: None)

    state, details = rust_bridge.get_rust_ext_status(required_symbols=("pipeline_concurrency_stats_get",))

    assert state == "import_error"
    assert "test import failure" in details


def test_rust_ext_status_missing_symbol(monkeypatch):
    monkeypatch.setattr(rust_bridge, "_EXT_IMPORT_ERROR", None)
    fake_ext = types.SimpleNamespace()
    monkeypatch.setattr(rust_bridge, "_load_ext", lambda: fake_ext)

    state, details = rust_bridge.get_rust_ext_status(required_symbols=("pipeline_concurrency_stats_get",))

    assert state == "missing_symbol"
    assert "pipeline_concurrency_stats_get" in details


def test_rust_ext_status_loaded(monkeypatch):
    monkeypatch.setattr(rust_bridge, "_EXT_IMPORT_ERROR", None)
    fake_ext = types.SimpleNamespace(pipeline_concurrency_stats_get=lambda: (0.0, 0.0, 0))
    monkeypatch.setattr(rust_bridge, "_load_ext", lambda: fake_ext)

    state, details = rust_bridge.get_rust_ext_status(required_symbols=("pipeline_concurrency_stats_get",))

    assert state == "loaded"
    assert details == "extension loaded"
