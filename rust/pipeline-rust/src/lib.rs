#![allow(clippy::useless_conversion)]
#![allow(clippy::manual_div_ceil)]

mod buzhash;
mod chunker;
mod compress_codecs;

use compress_codecs::compress_chunk;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyDict};
use rayon::ThreadPoolBuilder;
use std::borrow::Cow;
use std::sync::Mutex;
use std::time::Instant;

fn effective_jobs(jobs: Option<usize>) -> usize {
    match jobs {
        Some(0) | None => std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1),
        Some(n) => n,
    }
}

/// Last parallel pipeline timing: (pool_acquire_ms, parallel_copy_ms, total_ms).
static LAST_PIPE_TIMING: Mutex<Option<(f64, f64, f64)>> = Mutex::new(None);

/// After the last `parallel_copy`: (rayon_chunk_pairs, input_len, jobs_threads).
static LAST_PARALLEL_STATS: Mutex<Option<(usize, usize, usize)>> = Mutex::new(None);

#[derive(Clone, Copy, Debug)]
struct ConcurrencyStats {
    in_flight: i64,
    max_in_flight: i64,
    samples: u64,
    sum_in_flight: f64,
    active_ms: f64,
    last_update: Option<Instant>,
}

impl Default for ConcurrencyStats {
    fn default() -> Self {
        Self {
            in_flight: 0,
            max_in_flight: 0,
            samples: 0,
            sum_in_flight: 0.0,
            active_ms: 0.0,
            last_update: None,
        }
    }
}

static CONCURRENCY_STATS: Mutex<ConcurrencyStats> = Mutex::new(ConcurrencyStats {
    in_flight: 0,
    max_in_flight: 0,
    samples: 0,
    sum_in_flight: 0.0,
    active_ms: 0.0,
    last_update: None,
});

fn clear_parallel_stats() {
    if let Ok(mut g) = LAST_PARALLEL_STATS.lock() {
        *g = None;
    }
}

/// Borrow `bytes` without copying; otherwise materialize a `Vec` (e.g. memoryview).
fn bytes_cow<'py>(obj: &'py Bound<'py, PyAny>) -> PyResult<Cow<'py, [u8]>> {
    if let Ok(b) = obj.downcast::<PyBytes>() {
        return Ok(Cow::Borrowed(b.as_bytes()));
    }
    Ok(Cow::Owned(obj.extract::<Vec<u8>>()?))
}

#[pyfunction(signature = (meta, data, ctype, clevel, jobs=None))]
fn compress<'py>(
    py: Python<'py>,
    meta: &Bound<'py, PyDict>,
    data: &Bound<'py, PyAny>,
    ctype: u8,
    clevel: u8,
    jobs: Option<usize>,
) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyBytes>)> {
    let _threads = effective_jobs(jobs).max(1);
    let out_meta = meta.copy()?;
    let cow = bytes_cow(data)?;
    let input = cow.as_ref();
    out_meta.set_item("size", input.len())?;

    let (compressed, out_ctype, out_clevel) = py.allow_threads(|| compress_chunk(input, ctype, clevel))?;

    out_meta.set_item("ctype", out_ctype)?;
    out_meta.set_item("clevel", out_clevel)?;
    out_meta.set_item("csize", compressed.len())?;

    Ok((out_meta, PyBytes::new_bound(py, &compressed)))
}

#[pyfunction(signature = (id_bytes, data, jobs=None))]
fn encrypt<'py>(
    py: Python<'py>,
    id_bytes: &Bound<'py, PyBytes>,
    data: &Bound<'py, PyAny>,
    jobs: Option<usize>,
) -> PyResult<Bound<'py, PyBytes>> {
    let _ = (py, id_bytes, data, jobs);
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "rust encrypt is not implemented safely without key material; falling back to Python key.encrypt",
    ))
}

/// Combined path is intentionally disabled until real Rust-side encryption is available.
#[pyfunction(signature = (id_bytes, meta, data, ctype, clevel, jobs=None))]
fn compress_encrypt<'py>(
    py: Python<'py>,
    id_bytes: &Bound<'py, PyBytes>,
    meta: &Bound<'py, PyDict>,
    data: &Bound<'py, PyAny>,
    ctype: u8,
    clevel: u8,
    jobs: Option<usize>,
) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
    let _ = (py, id_bytes, meta, data, ctype, clevel, jobs);
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "rust compress_encrypt is disabled until Rust encryption parity exists; use separate compress + Python encrypt fallback",
    ))
}

#[pyfunction(signature = (iterations, jobs=None))]
fn benchmark_pool_setup(iterations: usize, jobs: Option<usize>) -> PyResult<Vec<f64>> {
    let threads = effective_jobs(jobs).max(1);
    let mut runs_ms = Vec::with_capacity(iterations);
    for _ in 0..iterations {
        let start = Instant::now();
        let _ = ThreadPoolBuilder::new()
            .num_threads(threads)
            .build()
            .expect("failed to build rayon pool in benchmark");
        runs_ms.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    Ok(runs_ms)
}

#[pyfunction(signature = ())]
fn pipeline_timing_get() -> PyResult<(f64, f64, f64)> {
    let g = LAST_PIPE_TIMING
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("pipeline timing mutex poisoned"))?;
    Ok(g.unwrap_or((0.0, 0.0, 0.0)))
}

#[pyfunction(signature = ())]
fn pipeline_timing_reset() -> PyResult<()> {
    if let Ok(mut g) = LAST_PIPE_TIMING.lock() {
        *g = None;
    }
    clear_parallel_stats();
    Ok(())
}

/// Concurrency snapshot after the last parallel `parallel_copy`: (pool_ms, copy_ms, total_ms, rayon_pairs, input_len, thread_count).
/// `rayon_pairs` is the number of 1MiB source/sink slice pairs (ceil(len/1MiB)). Zeros if no parallel copy ran since last reset.
#[pyfunction(signature = ())]
fn pipeline_parallel_stats_get() -> PyResult<(f64, f64, f64, usize, usize, usize)> {
    let t = LAST_PIPE_TIMING
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("pipeline timing mutex poisoned"))?;
    let (a, b, c) = t.unwrap_or((0.0, 0.0, 0.0));
    let s = LAST_PARALLEL_STATS
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("parallel stats mutex poisoned"))?;
    let (pairs, ilen, thr) = s.unwrap_or((0, 0, 0));
    Ok((a, b, c, pairs, ilen, thr))
}

#[pyfunction(signature = ())]
fn pipeline_concurrency_stats_reset() -> PyResult<()> {
    let mut stats = CONCURRENCY_STATS
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrency stats mutex poisoned"))?;
    *stats = ConcurrencyStats::default();
    Ok(())
}

#[pyfunction(signature = (in_flight))]
fn pipeline_concurrency_inflight_update(in_flight: i64) -> PyResult<()> {
    let now = Instant::now();
    let mut stats = CONCURRENCY_STATS
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrency stats mutex poisoned"))?;

    if let Some(last) = stats.last_update {
        if stats.in_flight > 0 {
            stats.active_ms += now.duration_since(last).as_secs_f64() * 1000.0;
        }
    }
    stats.last_update = Some(now);
    stats.in_flight = in_flight.max(0);
    stats.max_in_flight = stats.max_in_flight.max(stats.in_flight);
    stats.samples += 1;
    stats.sum_in_flight += stats.in_flight as f64;
    Ok(())
}

#[pyfunction(signature = ())]
fn pipeline_concurrency_stats_get() -> PyResult<(f64, f64, i64)> {
    let now = Instant::now();
    let mut stats = CONCURRENCY_STATS
        .lock()
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("concurrency stats mutex poisoned"))?;

    if let Some(last) = stats.last_update {
        if stats.in_flight > 0 {
            stats.active_ms += now.duration_since(last).as_secs_f64() * 1000.0;
            stats.last_update = Some(now);
        }
    }
    let avg = if stats.samples == 0 {
        0.0
    } else {
        stats.sum_in_flight / stats.samples as f64
    };
    Ok((stats.active_ms, avg, stats.max_in_flight))
}

#[pymodule]
fn borg_rust_ext(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compress, m)?)?;
    m.add_function(wrap_pyfunction!(encrypt, m)?)?;
    m.add_function(wrap_pyfunction!(compress_encrypt, m)?)?;
    m.add_function(wrap_pyfunction!(benchmark_pool_setup, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_timing_get, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_timing_reset, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_parallel_stats_get, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_concurrency_stats_reset, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_concurrency_inflight_update, m)?)?;
    m.add_function(wrap_pyfunction!(pipeline_concurrency_stats_get, m)?)?;
    buzhash::register(m)?;
    chunker::register(m)?;
    Ok(())
}
