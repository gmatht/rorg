//! Match `compress.pyx` DecidingCompressor + CompressorBase.compress (non-legacy) for simple codecs.

use flate2::write::ZlibEncoder;
use flate2::Compression;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::PyErr;
use std::io::Write;
use xz2::stream::{Check, Stream};
use xz2::write::XzEncoder;

/// Borg2 compressor IDs (`compress.pyx`).
const CT_NONE: u8 = 0x00;
const CT_LZ4: u8 = 0x01;
const CT_LZMA: u8 = 0x02;
const CT_ZSTD: u8 = 0x03;
const CT_ZLIB: u8 = 0x05;
const LEVEL_NONE: u8 = 255;

fn py_err(msg: impl Into<String>) -> PyErr {
    PyErr::new::<PyRuntimeError, _>(msg.into())
}

/// Returns `(payload, ctype, clevel)` matching Python `Compressor.compress` for the requested profile.
pub fn compress_chunk(data: &[u8], want_ctype: u8, want_clevel: u8) -> Result<(Vec<u8>, u8, u8), PyErr> {
    match want_ctype {
        CT_NONE => Ok((data.to_vec(), CT_NONE, want_clevel)),
        CT_LZ4 => {
            let buf = lz4::block::compress(data, None, false).map_err(|e| py_err(format!("lz4: {e}")))?;
            if buf.len() < data.len() {
                Ok((buf, CT_LZ4, want_clevel))
            } else {
                Ok((data.to_vec(), CT_NONE, LEVEL_NONE))
            }
        }
        CT_ZSTD => {
            let level = i32::from(want_clevel);
            let out = zstd::encode_all(data, level).map_err(|e| py_err(format!("zstd: {e}")))?;
            if out.len() < data.len() {
                Ok((out, CT_ZSTD, want_clevel))
            } else {
                Ok((data.to_vec(), CT_NONE, LEVEL_NONE))
            }
        }
        CT_LZMA => {
            let stream = Stream::new_easy_encoder(u32::from(want_clevel), Check::None)
                .map_err(|e| py_err(format!("xz2 stream: {e}")))?;
            let mut enc = XzEncoder::new_stream(Vec::new(), stream);
            enc.write_all(data).map_err(|e| py_err(format!("xz2 write: {e}")))?;
            let out = enc.finish().map_err(|e| py_err(format!("xz2 finish: {e}")))?;
            if out.len() < data.len() {
                Ok((out, CT_LZMA, want_clevel))
            } else {
                Ok((data.to_vec(), CT_NONE, LEVEL_NONE))
            }
        }
        CT_ZLIB => {
            let mut enc = ZlibEncoder::new(Vec::new(), Compression::new(u32::from(want_clevel)));
            enc.write_all(data).map_err(|e| py_err(format!("zlib: {e}")))?;
            let out = enc.finish().map_err(|e| py_err(format!("zlib finish: {e}")))?;
            if out.len() < data.len() {
                Ok((out, CT_ZLIB, want_clevel))
            } else {
                Ok((data.to_vec(), CT_NONE, LEVEL_NONE))
            }
        }
        _ => Err(PyErr::new::<PyValueError, _>(format!(
            "unsupported compressor type id: {}",
            want_ctype
        ))),
    }
}
