# Rust extension usage (`borg_rust_ext`)

This repository includes an optional Rust extension at `rust/pipeline-rust/`.
Borg works without it, but you can enable experimental Rust-backed pipeline
paths for development and benchmarking.

## 1) Build the extension

From the repository root:

```shell
cd rust/pipeline-rust
cargo build --release
```

The built module must be importable as `borg.borg_rust_ext` in your Python
environment (packaging/layout can vary by setup).

## 2) Enable Rust pipeline toggles

Set environment variables before running Borg:

```shell
set BORG_RUST_PIPELINE=1
set BORG_RUST_COMPRESS=1
set BORG_RUST_ENCRYPT=1
set BORG_RUST_PIPELINE_COMBINED=1
set BORG_RUST_CHUNKER=1
```

Meaning:

- `BORG_RUST_PIPELINE=1`: enable Rust bridge attempts.
- `BORG_RUST_COMPRESS=1`: enable Rust compression entry point.
- `BORG_RUST_ENCRYPT=1`: enable Rust encryption entry point.
- `BORG_RUST_PIPELINE_COMBINED=1`: enable combined compress+encrypt path.
- `BORG_RUST_CHUNKER=1`: use Rust buzhash chunker where supported.

If the extension is missing or a function is unavailable, Borg falls back to
the existing Python/Cython paths.

## 3) Control jobs for `borg create`

Use `-j N` / `--jobs N` with `borg create`:

- default: CPU count
- `-j1`: strict serial mode

Use `--rust-stats` with `borg create` (or `./rorg.sh create`) to print Rust pipeline
concurrency metrics at the end of the run:

- active time in milliseconds while there were async chunk puts in flight
- average tasks in flight
- maximum tasks in flight

## 4) Optional timing/stat helpers

When Rust parallel copy is active, these helpers can be used from Python:

- `borg.rust_bridge.pipeline_timing_get()`
- `borg.rust_bridge.pipeline_timing_reset()`
- `borg.rust_bridge.pipeline_parallel_stats_get()`

## 5) Validate Rust crate quality

```shell
cd rust/pipeline-rust
cargo check
cargo clippy --all-targets -- -D warnings
```

## Related docs

- `docs/development/rust_build.rst`
- `docs/misc/rust_pipeline.rst`
- `rust_benchmark.md`

## Current migration status

- Rust `compress` is implemented for supported codecs (`none`, `lz4`, `zstd`, `lzma`, `zlib`).
- Rust `encrypt` is intentionally disabled for now and falls back to Python key encryption.
- Rust `compress_encrypt` is intentionally disabled for now and falls back to the split safe path.

Reason: Borg key material and encryption envelopes are currently owned by Python key objects; the
Rust extension does not yet receive enough key context to provide crypto-parity encryption.
