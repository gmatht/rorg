# Rust compress -> encrypt migration benchmarks (64MiB)

This file tracks benchmark results for each migration checkpoint.

## Benchmark protocol

- Workload: create a fresh repo and one archive with one 64MiB file (unless using `--num-files` in `scripts/rust_benchmark.py`).
- Compression set: `none`, `zstd,3`, `lzma,6`.
- Runs: 5 runs per compression, record all values and median.
- From Step 4 on: include default jobs (`nCPUs`) and `-j1`.
- **Payload modes** (`--payload-mode`): `zero` (legacy, all NUL), `random` (incompressible), `mixed` (non-trivial repeating pattern), `text` (compressible). All-zero payloads interact strongly with dedup and can make codec differences hard to interpret; use `random` or `text` to stress compression.
- **Parallelism timing**: when the Rust extension runs a parallel copy (`jobs` > 1), call `borg.rust_bridge.pipeline_timing_get()` after a run to read `(pool_acquire_ms, parallel_copy_ms, total_ms)`; `pipeline_timing_reset()` clears the last sample. The Rust compress path is still a placeholder identity unless real codecs are wired in—`lzma` vs `none` wall time may look similar when overhead dominates.
- **Parallel stats** (`pipeline_parallel_stats_get()`): returns `(pool_ms, copy_ms, total_ms, rayon_pairs, input_len, thread_count)`. `rayon_pairs` is `ceil(input_len / 1MiB)` slice pairs for the last parallel memcpy in the Rust pipeline (0 if `-j1` / serial path). Use after `pipeline_timing_reset()` then one `borg create` with Rust pipeline enabled to see whether Rayon actually split work.

## Payload file contents (`scripts/rust_benchmark.py`)

| `--payload-mode` | What gets written | Good for |
|------------------|---------------------|----------|
| `zero` (default) | All `NUL` bytes in up to 1 MiB writes | Baseline; heavy dedup; can flatten codec differences. |
| `random` | `secrets.token_bytes` per chunk | Incompressible data; stresses hashing/encryption paths. |
| `mixed` | Repeating `0..255` byte pattern | Compressible but not all-zero; different from pure dedup of zeros. |
| `text` | Repeated English line | Log-like compressible data; good for `zstd` / `lzma` vs `none`. |

Total bytes archived per run: `--size-mib` × `--num-files` (each file is `size-mib` mebibytes).

## Concurrency checklist (Rust pipeline)

1. Build/install `borg_rust_ext` (Rust extension).
2. Set `BORG_RUST_PIPELINE=1` and the stage flags you are testing (`BORG_RUST_COMPRESS`, etc.).
3. Call `rust_bridge.pipeline_timing_reset()` then run `borg create` with the same repo/data.
4. Read `rust_bridge.pipeline_parallel_stats_get()`:
   - **`-j1`**: `rayon_pairs` should stay **0** (serial fast path; no parallel memcpy).
   - **Default jobs or `-j N` with N>1** and payload **≥ ~2 MiB**: expect **non-zero** `rayon_pairs` and `parallel_copy_ms` when the Rust path runs `parallel_copy`.
5. Borg’s **content-defined chunking** (buzhash in Cython) still runs in Python; parallelism here is **per `RepoObj.format` buffer**, not across file chunks.

## Command template

```shell
borg --repo <repo_path> repo-create --encryption repokey-aes-ocb
borg --repo <repo_path> create --compression <compression> <archive_name> <path_to_64mib_file>
```

## Step 1 - Rust compression enabled

Environment:
- `BORG_RUST_PIPELINE=1`
- `BORG_RUST_COMPRESS=1`
- `BORG_RUST_ENCRYPT=0`
- `BORG_RUST_PIPELINE_COMBINED=0`

Results:
- TODO

## Step 2 - Rust encryption enabled

Environment:
- `BORG_RUST_PIPELINE=1`
- `BORG_RUST_COMPRESS=1`
- `BORG_RUST_ENCRYPT=1`
- `BORG_RUST_PIPELINE_COMBINED=0`

Results:
- TODO

## Step 3 - Combined Rust fast path

Environment:
- `BORG_RUST_PIPELINE=1`
- `BORG_RUST_COMPRESS=1`
- `BORG_RUST_ENCRYPT=1`
- `BORG_RUST_PIPELINE_COMBINED=1`

Results:
- TODO

## Step 4 - Rayon jobs + `-jN`

Compare:
- default jobs (`nCPUs`)
- `-j1` strict serial

Results:
- TODO

## Step 5 - Default switch / hardening

## Step 0 - Baseline (WSL)

### none
- runs: 8.344s, 7.068s, 7.503s, 6.863s, 6.613s
- median: 7.068s

### zstd,3
- runs: 6.748s, 6.327s, 6.152s, 6.093s, 6.541s
- median: 6.327s

### lzma,6
- runs: 6.586s, 7.823s, 7.038s, 8.067s, 7.112s
- median: 7.112s


## Step 1 - Rust compression enabled (WSL)

### none
- runs: 8.923s, 6.975s, 7.023s, 8.205s, 7.226s
- median: 7.226s

### zstd,3
- runs: 7.553s, 8.548s, 8.127s, 8.029s, 8.550s
- median: 8.127s

### lzma,6
- runs: 9.271s, 9.348s, 8.979s, 10.395s, 10.118s
- median: 9.348s


## Step 2 - Rust encryption enabled (WSL)

### none
- runs: 10.285s, 10.317s, 10.348s, 10.217s, 10.008s
- median: 10.285s

### zstd,3
- runs: 9.979s, 10.059s, 10.222s, 9.551s, 9.161s
- median: 9.979s

### lzma,6
- runs: 9.135s, 9.436s, 10.015s, 10.458s, 10.546s
- median: 10.015s


## Step 3 - Combined Rust fast path (WSL)

### none
- runs: 11.420s, 10.199s, 10.249s, 9.997s, 10.685s
- median: 10.249s

### zstd,3
- runs: 9.979s, 10.286s, 10.538s, 10.560s, 10.477s
- median: 10.477s

### lzma,6
- runs: 10.833s, 10.929s, 11.365s, 11.097s, 10.913s
- median: 10.929s


## Step 4 - Rayon jobs default (WSL)

### none
- runs: 12.567s, 9.051s, 8.939s, 9.896s, 10.432s
- median: 9.896s

### zstd,3
- runs: 10.423s, 9.637s, 9.939s, 8.706s, 9.731s
- median: 9.731s

### lzma,6
- runs: 10.025s, 9.834s, 9.808s, 9.668s, 9.301s
- median: 9.808s


## Step 4 - Rayon jobs -j1 (WSL)

### none
- runs: 10.203s, 9.422s, 9.296s, 9.411s, 9.542s
- median: 9.422s

### zstd,3
- runs: 9.415s, 9.962s, 9.871s, 9.495s, 10.442s
- median: 9.871s

### lzma,6
- runs: 11.539s, 11.511s, 11.067s, 11.679s, 11.647s
- median: 11.539s


## Step 5 - Default switch and hardening (WSL)

### none
- runs: 12.437s, 11.009s, 12.278s, 11.612s, 12.035s
- median: 12.035s

### zstd,3
- runs: 11.047s, 11.502s, 11.019s, 10.866s, 8.978s
- median: 11.019s

### lzma,6
- runs: 8.858s, 8.884s, 8.163s, 8.423s, 8.170s
- median: 8.423s

## Final delta vs baseline (WSL medians)

Baseline reference: `Step 0 - Baseline (WSL)`.

| Compression | Step 0 Baseline | Step 5 Final | Delta (Final - Baseline) | Interpretation |
|---|---:|---:|---:|---|
| `none` | 7.068s | 12.035s | +4.967s | Regression |
| `zstd,3` | 6.327s | 11.019s | +4.692s | Regression |
| `lzma,6` | 7.112s | 8.423s | +1.311s | Regression |


## Optimized Step 0 - Baseline (WSL)

### none
- runs: 13.308s, 12.262s, 11.756s, 11.697s, 11.915s
- median: 11.915s

### zstd,3
- runs: 11.597s, 12.175s, 12.068s, 11.699s, 11.507s
- median: 11.699s

### lzma,6
- runs: 12.624s, 12.305s, 12.771s, 13.525s, 12.210s
- median: 12.624s


## Optimized Step 5 - Default switch and hardening (WSL)

### none
- runs: 14.021s, 12.608s, 11.955s, 12.722s, 12.417s
- median: 12.608s

### zstd,3
- runs: 10.619s, 11.544s, 12.409s, 11.924s, 12.331s
- median: 11.924s

### lzma,6
- runs: 13.321s, 13.369s, 13.083s, 12.187s, 12.250s
- median: 13.083s


## Optimized Step 4 - Rayon jobs default (WSL)

### none
- runs: 13.204s, 12.132s, 11.303s, 11.261s, 11.843s
- median: 11.843s

### zstd,3
- runs: 10.873s, 10.917s, 11.297s, 10.955s, 10.953s
- median: 10.953s

### lzma,6
- runs: 11.777s, 15.843s, 14.781s, 15.197s, 16.117s
- median: 15.197s


## Optimized Step 4 - Rayon jobs -j1 (WSL)

### none
- runs: 14.267s, 14.309s, 14.687s, 17.050s, 16.497s
- median: 14.687s

### zstd,3
- runs: 14.373s, 12.983s, 13.625s, 12.351s, 12.343s
- median: 12.983s

### lzma,6
- runs: 13.292s, 12.496s, 15.079s, 14.669s, 12.926s
- median: 13.292s

## Optimized Step 4a - Rayon pool setup overhead (WSL)

Source:
- `cargo run --release --bin pool_setup_bench -- 30 1 2 4 8`

| jobs | median_pool_setup_ms | notes |
|---:|---:|---|
| 1 | 0.104 | low setup overhead |
| 2 | 0.208 | roughly linear increase from 1 thread |
| 4 | 0.310 | one large outlier observed (`6.222ms`), median stable |
| 8 | 0.830 | setup cost rises with thread count, one outlier (`3.083ms`) |

Interpretation:
- Pool setup itself is sub-millisecond at typical thread counts.
- The larger regressions in end-to-end benchmarks are therefore dominated by bridge/data-path overhead and non-optimized placeholder compute, not just pool setup creation.

## Optimized final delta vs optimized baseline (WSL medians)

Baseline reference: `Optimized Step 0 - Baseline (WSL)`.

| Compression | Optimized Baseline | Optimized Step 5 Final | Delta (Final - Baseline) | Interpretation |
|---|---:|---:|---:|---|
| `none` | 11.915s | 12.608s | +0.693s | Slight regression |
| `zstd,3` | 11.699s | 11.924s | +0.225s | Near baseline |
| `lzma,6` | 12.624s | 13.083s | +0.459s | Moderate regression |

