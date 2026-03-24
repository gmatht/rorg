Rust Pipeline Migration Flags
=============================

The compress -> encrypt migration can be tested with runtime flags.

Environment toggles
-------------------

- ``BORG_RUST_PIPELINE=1``: enable Rust bridge attempts.
- ``BORG_RUST_COMPRESS=1``: enable Rust compression function.
- ``BORG_RUST_ENCRYPT=1``: enable Rust encryption function.
- ``BORG_RUST_PIPELINE_COMBINED=1``: enable combined compress+encrypt function.
- ``BORG_RUST_CHUNKER=1``: use the Rust buzhash chunker for ``borg create`` (and other paths that
  call ``get_chunker`` with ``algo="buzhash"``), including **sparse** files (``SEEK_HOLE`` / ``SEEK_DATA``)
  via ``RustBuzhashChunker.feed_zeros``. Falls back to Cython when the extension is missing or the
  algorithm is not buzhash.

If the extension module is unavailable or a function is not exported, Borg falls back
to the Python/Cython path.

FFI direction (Python / Rust)
-----------------------------

Borg calls **into** the Rust extension (PyO3); the extension does **not** invoke Python chunkers,
compressors, or other application callbacks. Rust receives buffers and returns Python objects;
any future “call Python from Rust” design would be explicit and is not used today.

Jobs control
------------

``borg create`` supports:

- ``-j N`` / ``--jobs N``: job count for the Rust pipeline backend.
- default: number of CPUs.
- ``-j1``: strict serial mode.
- ``--rust-stats``: print Rust concurrency stats at end of ``create``:
  active time (ms), average tasks in flight, max tasks in flight.

Example::

    ./rorg.sh create --rust-stats ::test1 local/in/ -C lzma,9

Parallel copy timing (extension)
--------------------------------

When the Rust extension performs a parallel buffer copy internally, it records the last timings
in milliseconds:

- ``borg.rust_bridge.pipeline_timing_get()`` → ``(pool_acquire_ms, parallel_copy_ms, total_ms)``
  or ``None`` if the extension is missing.
- ``borg.rust_bridge.pipeline_timing_reset()`` clears the stored sample.

When no parallel copy has run, these values stay zero after reset.

Benchmark script payloads
-------------------------

``scripts/rust_benchmark.py`` supports ``--payload-mode`` (``zero``, ``random``, ``mixed``, ``text``)
and ``--num-files`` to vary workload. All-zero files can make dedup dominate and flatten codec
comparisons; prefer ``random`` or ``text`` when measuring compression differences.

Parallel copy stats (extension)
--------------------------------

- ``borg.rust_bridge.pipeline_parallel_stats_get()`` → ``(pool_ms, copy_ms, total_ms, rayon_pairs, input_len, thread_count)``
  or ``None`` if the extension is missing. ``rayon_pairs`` is ``ceil(input_len / 1MiB)`` for the last
  parallel memcpy (0 when the serial path ran). ``pipeline_timing_reset()`` clears timing and stats.

Buzhash primitives in Rust (parity with Cython)
-----------------------------------------------

The extension exposes rolling-hash helpers matching ``borg.chunkers.buzhash``:

- ``borg.rust_bridge.rust_buzhash(data, seed)`` — same role as ``borg.chunkers.buzhash.buzhash``.
- ``borg.rust_bridge.rust_buzhash_update(sum, remove, add, len, seed)`` — same as ``buzhash_update``.

Content-defined chunking (Rust)
-------------------------------

- ``borg.rust_bridge.buzhash_chunk_bytes(data, seed, chunk_min_exp, chunk_max_exp, hash_mask_bits, hash_window_size)``
  returns a ``list[bytes]`` matching Cython ``Chunker`` on a dense in-memory buffer (same chunk
  boundaries as ``borg.chunkers.buzhash.Chunker`` for ordinary files). Returns ``None`` if the
  extension is missing.

- With ``BORG_RUST_CHUNKER=1``, ``borg.chunkers.get_chunker("buzhash", ...)`` returns
  ``borg.chunkers.rust_buzhash.RustChunker`` for both dense and sparse inputs (when ``sparse=True``,
  hole ranges are injected as logical zeros in Rust without allocating ``len(hole)`` Python
  ``bytes``). ``RustChunker.chunkify(..., fmap=...)`` falls back to Cython when a custom file map
  is supplied.

Compression and encryption status
---------------------------------

- Rust ``compress`` implements real codec work for ``none``, ``lz4``, ``zstd``, ``lzma``, and
  ``zlib`` with Borg metadata parity (``ctype`` / ``clevel`` / ``csize`` updates).
- Rust ``encrypt`` is currently **disabled** in the extension. ``rust_bridge.encrypt(...)`` falls
  back to Python key encryption when the extension raises ``NotImplementedError``.
- Rust ``compress_encrypt`` is currently **disabled** in the extension for the same reason.
  ``rust_bridge.compress_encrypt(...)`` falls back to the safe split path (Rust compress + Python
  encryption when enabled by flags).

This keeps security semantics in Python key code until Rust receives explicit key material and
full crypto parity coverage.

Parallelism and profiling
-------------------------

- **Per-chunk**: ``--jobs`` controls Rayon inside a single ``RepoObj.format`` buffer (see
  ``pipeline_parallel_stats_get()``). The archiver still walks file chunks **sequentially** in Python.
- **Before adding** thread pools around multiple chunks: profile with ``perf``, ``htop``, and Borg’s
  own stats; confirm chunk order and IDs remain deterministic for the repository format.

See ``docs/development/rust_build.rst`` for building and linting the extension.
