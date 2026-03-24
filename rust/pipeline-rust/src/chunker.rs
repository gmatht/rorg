//! Content-defined chunker matching `borg.chunkers.buzhash.Chunker` (`buzhash.pyx`).
//! Streaming source: `feed_bytes` appends real data; `feed_zeros` appends logical NUL runs
//! (sparse holes / CH_ALLOC) without allocating those bytes on the Python side—mirrors Cython
//! `memset` in `buzhash.pyx` `fill()`.

use crate::buzhash::{buzhash_raw, buzhash_update_raw, init_table};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes};

/// Result of one `process()` step: one emitted chunk, need more input bytes, or end of stream.
#[derive(Debug)]
pub enum ProcessResult {
    Chunk(Vec<u8>),
    /// Caller must append more with `feed_bytes` (or call `set_input_closed` if no more data exists).
    NeedMore,
    /// No more chunks (`StopIteration` in Python).
    Exhausted,
    /// Runtime invariant failure.
    Error(String),
}

/// Mirrors Cython `Chunker` + `fill`/`process` for dense and sparse (zero-run) byte streams.
pub struct BuzhashChunker {
    chunk_mask: u32,
    table: [u32; 256],
    data: Vec<u8>,
    buf_size: usize,
    min_size: usize,
    window_size: usize,
    remaining: usize,
    position: usize,
    last: usize,
    done: bool,
    eof: bool,
    /// When false, running out of buffered `source` bytes does not imply EOF (streaming).
    input_closed: bool,
    bytes_read: u64,
    bytes_yielded: u64,
    read_pos: usize,
    source: Vec<u8>,
    /// Bytes of logical zeros still to inject before consuming `source` (same order as `feed_*` calls).
    zero_pending: u64,
}

impl BuzhashChunker {
    pub fn new(
        seed: i32,
        chunk_min_exp: u32,
        chunk_max_exp: u32,
        hash_mask_bits: u32,
        hash_window_size: usize,
    ) -> Self {
        let min_size = 1usize << chunk_min_exp;
        let max_size = 1usize << chunk_max_exp;
        assert!(
            hash_window_size + min_size < max_size,
            "too small max_size"
        );
        let buf_size = max_size;
        let data = vec![0u8; buf_size];
        let table = init_table(seed as u32);
        Self {
            chunk_mask: (1u32 << hash_mask_bits) - 1,
            table,
            data,
            buf_size,
            min_size,
            window_size: hash_window_size,
            remaining: 0,
            position: 0,
            last: 0,
            done: false,
            eof: false,
            input_closed: false,
            bytes_read: 0,
            bytes_yielded: 0,
            read_pos: 0,
            source: Vec::new(),
            zero_pending: 0,
        }
    }

    pub fn chunkify_bytes(&mut self, source: Vec<u8>) {
        self.source = source;
        self.read_pos = 0;
        self.input_closed = true;
        self.done = false;
        self.eof = false;
        self.remaining = 0;
        self.bytes_read = 0;
        self.bytes_yielded = 0;
        self.position = 0;
        self.last = 0;
        self.zero_pending = 0;
    }

    /// Append bytes from a streaming read (e.g. `FileReader.read`). Bounded memory: drops consumed prefix.
    pub fn feed_bytes(&mut self, chunk: &[u8]) {
        self.source.extend_from_slice(chunk);
        self.compact_source();
    }

    /// Append `n` logical zero bytes (sparse / hole ranges) without storing them in `source`.
    pub fn feed_zeros(&mut self, n: usize) {
        self.zero_pending = self.zero_pending.saturating_add(n as u64);
    }

    pub fn set_input_closed(&mut self) {
        self.input_closed = true;
    }

    fn compact_source(&mut self) {
        const MAX_BEFORE_COMPACT: usize = 256 * 1024;
        if self.read_pos >= MAX_BEFORE_COMPACT {
            let keep = self.source.len().saturating_sub(self.read_pos);
            if keep > 0 {
                self.source.copy_within(self.read_pos.., 0);
            }
            self.source.truncate(keep);
            self.read_pos = 0;
        }
    }

    /// Simulate `FileReader.read(n)` with at most `READER_BLOCK` per call (Cython default).
    fn fill(&mut self) {
        let len_move = self.position + self.remaining - self.last;
        if self.last > 0 && len_move > 0 {
            self.data.copy_within(self.last..self.last + len_move, 0);
        }
        self.position -= self.last;
        self.last = 0;

        let mut space = self.buf_size.saturating_sub(self.position + self.remaining);
        if self.eof || space == 0 {
            return;
        }

        while space > 0 && self.zero_pending > 0 {
            let zp = usize::try_from(self.zero_pending).unwrap_or(usize::MAX);
            let take = space.min(zp);
            let dst = self.position + self.remaining;
            self.data[dst..dst + take].fill(0);
            self.remaining += take;
            self.bytes_read += take as u64;
            self.zero_pending -= take as u64;
            space = self.buf_size.saturating_sub(self.position + self.remaining);
        }

        if space == 0 {
            return;
        }

        let avail = self.source.len().saturating_sub(self.read_pos);
        if avail == 0 {
            if self.input_closed && self.zero_pending == 0 {
                self.eof = true;
            }
            return;
        }
        let n = space.min(avail);
        let dst = self.position + self.remaining;
        self.data[dst..dst + n].copy_from_slice(&self.source[self.read_pos..self.read_pos + n]);
        self.remaining += n;
        self.bytes_read += n as u64;
        self.read_pos += n;
    }

    /// One `process()` call for batch API (all input already in buffer with `input_closed`).
    pub fn process(&mut self) -> Result<Option<Vec<u8>>, String> {
        match self.process_inner() {
            ProcessResult::Chunk(v) => Ok(Some(v)),
            ProcessResult::NeedMore => Err("buzhash_chunk_bytes: unexpected NeedMore".to_string()),
            ProcessResult::Exhausted => Ok(None),
            ProcessResult::Error(msg) => Err(msg),
        }
    }

    pub fn process_inner(&mut self) -> ProcessResult {
        let chunk_mask = self.chunk_mask;
        let min_size = self.min_size;
        let window_size = self.window_size;

        if self.done {
            if self.bytes_read == self.bytes_yielded {
                return ProcessResult::Exhausted;
            }
            return ProcessResult::Error("chunkifier byte count mismatch".to_string());
        }

        while self.remaining < min_size + window_size + 1 && !self.eof {
            self.fill();
        }

        if !self.eof && self.remaining < min_size + window_size + 1 {
            return ProcessResult::NeedMore;
        }

        if self.eof {
            self.done = true;
            if self.remaining > 0 {
                self.bytes_yielded += self.remaining as u64;
                let chunk = self.data[self.position..self.position + self.remaining].to_vec();
                return ProcessResult::Chunk(chunk);
            }
            if self.bytes_read == self.bytes_yielded {
                return ProcessResult::Exhausted;
            }
            return ProcessResult::Error("chunkifier byte count mismatch".to_string());
        }

        self.position += min_size;
        self.remaining -= min_size;
        let mut sum = buzhash_raw(
            &self.data[self.position..self.position + window_size],
            &self.table,
        );

        while self.remaining > window_size && (sum & chunk_mask != 0) {
            let mut p = self.position;
            let stop_at = p + self.remaining - window_size;

            while p < stop_at && (sum & chunk_mask != 0) {
                sum = buzhash_update_raw(
                    sum,
                    self.data[p],
                    self.data[p + window_size],
                    window_size,
                    &self.table,
                );
                p += 1;
            }

            let did_bytes = p - self.position;
            self.position += did_bytes;
            self.remaining -= did_bytes;

            if self.remaining <= window_size {
                self.fill();
            }
        }

        if self.remaining <= window_size {
            self.position += self.remaining;
            self.remaining = 0;
        }

        let old_last = self.last;
        self.last = self.position;
        let n = self.last - old_last;
        self.bytes_yielded += n as u64;
        ProcessResult::Chunk(self.data[old_last..old_last + n].to_vec())
    }

    pub fn is_finished(&self) -> bool {
        self.done && self.bytes_read == self.bytes_yielded
    }
}

/// Python-facing streaming chunker (see `borg.chunkers.rust_buzhash.RustChunker`).
#[pyclass(name = "RustBuzhashChunker")]
pub struct RustBuzhashChunkerPy {
    inner: BuzhashChunker,
}

#[pymethods]
impl RustBuzhashChunkerPy {
    #[new]
    #[pyo3(signature = (seed, chunk_min_exp, chunk_max_exp, hash_mask_bits, hash_window_size))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        seed: i32,
        chunk_min_exp: u32,
        chunk_max_exp: u32,
        hash_mask_bits: u32,
        hash_window_size: u32,
    ) -> Self {
        Self {
            inner: BuzhashChunker::new(
                seed,
                chunk_min_exp,
                chunk_max_exp,
                hash_mask_bits,
                hash_window_size as usize,
            ),
        }
    }

    fn feed(&mut self, data: &Bound<'_, PyAny>) -> PyResult<()> {
        if let Ok(pybytes) = data.downcast::<PyBytes>() {
            self.inner.feed_bytes(pybytes.as_bytes());
            return Ok(());
        }
        let owned: Vec<u8> = data.extract()?;
        self.inner.feed_bytes(&owned);
        Ok(())
    }

    /// Logical zero bytes from a sparse hole or CH_ALLOC range (no Python `bytes` allocation).
    #[pyo3(signature = (n))]
    fn feed_zeros(&mut self, n: usize) {
        self.inner.feed_zeros(n);
    }

    /// No more bytes will be passed via `feed` (EOF on the underlying stream).
    fn set_eof(&mut self) {
        self.inner.set_input_closed();
    }

    fn next_chunk<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<Option<Bound<'py, PyBytes>>> {
        match self.inner.process_inner() {
            ProcessResult::Chunk(v) => Ok(Some(PyBytes::new_bound(py, &v))),
            ProcessResult::NeedMore => Ok(None),
            ProcessResult::Exhausted => Ok(None),
            ProcessResult::Error(msg) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg)),
        }
    }

    fn is_finished(&self) -> bool {
        self.inner.is_finished()
    }
}

#[pyfunction(
    name = "buzhash_chunk_bytes",
    signature = (data, seed, chunk_min_exp, chunk_max_exp, hash_mask_bits, hash_window_size)
)]
fn buzhash_chunk_bytes_py(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    seed: i32,
    chunk_min_exp: u32,
    chunk_max_exp: u32,
    hash_mask_bits: u32,
    hash_window_size: u32,
) -> PyResult<Vec<Py<PyBytes>>> {
    let mut c = BuzhashChunker::new(
        seed,
        chunk_min_exp,
        chunk_max_exp,
        hash_mask_bits,
        hash_window_size as usize,
    );
    c.chunkify_bytes(data.as_bytes().to_vec());
    let mut chunks = Vec::new();
    while let Some(chunk) = c
        .process()
        .map_err(|msg| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg))?
    {
        chunks.push(PyBytes::new_bound(py, &chunk).unbind());
    }
    Ok(chunks)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(buzhash_chunk_bytes_py, m)?)?;
    m.add_class::<RustBuzhashChunkerPy>()?;
    Ok(())
}
