//! Bit-exact embedding parity vs Python for Xenova int8 BGE.
//!
//! Loads `tests/parity-fixtures/embedding_inputs.txt` (5 lines), embeds them
//! through `chunkshop-rs`'s embedder configured for
//! `Xenova/bge-base-en-v1.5-int8`, and asserts every output float matches the
//! committed Python reference (`tests/parity-fixtures/embedding_reference_bge_base_int8.bin`)
//! bitwise via `f32::to_bits()`.
//!
//! Skips cleanly if HuggingFace Hub is unreachable (no network), since the
//! embedder must download model files on first run.

use std::path::PathBuf;

use chunkshop::config::FastembedEmbedderConfig;
use chunkshop::embedder::FastembedEmbedder;

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn read_inputs() -> Vec<String> {
    let p = fixtures_dir().join("embedding_inputs.txt");
    let s = std::fs::read_to_string(&p)
        .unwrap_or_else(|e| panic!("read {}: {}", p.display(), e));
    s.lines()
        .filter(|l| !l.is_empty())
        .map(|l| l.to_string())
        .collect()
}

fn read_reference() -> (usize, usize, Vec<f32>) {
    let p = fixtures_dir().join("embedding_reference_bge_base_int8.bin");
    let bytes = std::fs::read(&p).unwrap_or_else(|e| panic!("read {}: {}", p.display(), e));
    assert!(bytes.len() >= 8, "reference file too small");
    let n = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) as usize;
    let dim = u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]) as usize;
    let total_floats = n * dim;
    let expected_size = 8 + total_floats * 4;
    assert_eq!(
        bytes.len(),
        expected_size,
        "reference file size {} != expected {} for n={} dim={}",
        bytes.len(),
        expected_size,
        n,
        dim,
    );
    let mut floats = Vec::with_capacity(total_floats);
    for i in 0..total_floats {
        let off = 8 + i * 4;
        floats.push(f32::from_le_bytes([
            bytes[off],
            bytes[off + 1],
            bytes[off + 2],
            bytes[off + 3],
        ]));
    }
    (n, dim, floats)
}

#[test]
fn rust_embeddings_byte_identical_to_python() {
    // Encourage deterministic ORT execution; bit-exactness can be sensitive
    // to thread-count-dependent reduction order.
    std::env::set_var("OMP_NUM_THREADS", "1");
    std::env::set_var("ORT_INTRA_OP_NUM_THREADS", "1");
    std::env::set_var("MKL_NUM_THREADS", "1");

    let inputs = read_inputs();
    let (n, dim, expected_flat) = read_reference();
    assert_eq!(inputs.len(), n, "input count must match reference n");

    let cfg = FastembedEmbedderConfig {
        model_name: "Xenova/bge-base-en-v1.5-int8".to_string(),
        dim,
        batch_size: 1,
        threads: Some(1),
    };

    // First-call download path; if no network, skip with a printed message
    // rather than failing — the test's purpose is parity, not download.
    let mut embedder = match FastembedEmbedder::new(cfg) {
        Ok(e) => e,
        Err(e) => {
            eprintln!(
                "skipping bit-exact parity test (embedder init failed — likely no network): {e:#}"
            );
            return;
        }
    };

    let actual = embedder
        .embed(inputs.clone())
        .expect("embed must succeed when init succeeded");

    assert_eq!(actual.len(), n, "produced {} vectors, expected {}", actual.len(), n);
    let mut mismatches: Vec<(usize, usize, f32, f32)> = Vec::new();
    for (i, v) in actual.iter().enumerate() {
        assert_eq!(v.len(), dim, "vector {} has dim {}, expected {}", i, v.len(), dim);
        for (j, &got) in v.iter().enumerate() {
            let exp = expected_flat[i * dim + j];
            if got.to_bits() != exp.to_bits() {
                mismatches.push((i, j, got, exp));
            }
        }
    }
    if !mismatches.is_empty() {
        let first = &mismatches[..mismatches.len().min(5)];
        panic!(
            "{} of {} floats differ. First mismatches (i, j, got, expected): {:?}",
            mismatches.len(),
            n * dim,
            first,
        );
    }
}
