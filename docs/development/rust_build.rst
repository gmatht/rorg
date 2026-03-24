Rust extension (``borg_rust_ext``)
====================================

The optional native module lives under ``rust/pipeline-rust/``. It is **not** required for
normal Borg operation; Python/Cython paths are used when the extension is missing.

Integration is one-way: Python calls Rust (PyO3); Rust does not call back into Python chunkers
or compressors. See ``docs/misc/rust_pipeline.rst`` for environment flags (including
``BORG_RUST_CHUNKER`` for create-time chunking).

Build (local)
-------------

.. code-block:: shell

   cd rust/pipeline-rust
   cargo build --release

The shared library must be importable as ``borg.borg_rust_ext`` (install layout depends on
your packaging; many workflows copy the ``.so`` / ``.pyd`` next to other ``borg`` modules).

Check / lint
------------

.. code-block:: shell

   cd rust/pipeline-rust
   cargo check
   cargo clippy --all-targets -- -D warnings

Continuous integration
----------------------

``.github/workflows/rust-pipeline.yml`` runs ``cargo clippy`` and ``cargo test`` when files under
``rust/pipeline-rust/`` change.

Python tests that need the extension (e.g. ``rust_chunker_parity_test``) skip when
``borg_rust_ext`` is not importable.
