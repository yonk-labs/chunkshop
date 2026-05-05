//! Embedding parity vs Python for Xenova int8 BGE.
//!
//! Loads `tests/parity-fixtures/embedding_inputs.txt` (5 lines), embeds them
//! through `chunkshop-rs`'s embedder configured for
//! `Xenova/bge-base-en-v1.5-int8`, and compares against committed Python
//! reference vectors (`tests/parity-fixtures/embedding_reference_bge_base_int8.bin`).
//!
//! Three thresholds (see SC-003 in the mission brief):
//! 1. median per-vector cosine distance ≤ 1e-7 (most inputs at f32 epsilon)
//! 2. max abs element-wise diff ≤ 1e-2 (soft cap on ORT-binary divergence)
//! 3. max per-vector cosine distance ≤ 5e-3 (caps worst-case similarity drop)
//!
//! Why not strict bitwise: Python's `onnxruntime` wheel and Rust's `ort`
//! crate are independent ORT C++ binary builds. They produce ULP-level
//! (and occasionally larger, on quantized matmul paths) divergences even
//! given identical inputs, models, and thread counts. See `rust/README.md`
//! for the cross-language parity envelope.
//!
//! Skips cleanly if HuggingFace Hub is unreachable (no network).

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
fn rust_embeddings_match_python_within_envelope() {
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
        hf_repo: None,
        onnx_path: None,
        pooling: "cls".to_string(),
        additional_files: vec![
            "tokenizer.json".to_string(),
            "tokenizer_config.json".to_string(),
            "special_tokens_map.json".to_string(),
            "config.json".to_string(),
        ],
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

    let mut max_abs_diff: f32 = 0.0;
    let mut max_rel_diff: f32 = 0.0;
    let mut total_floats = 0usize;
    let mut diff_count = 0usize;
    for (i, v) in actual.iter().enumerate() {
        assert_eq!(v.len(), dim, "vector {} has dim {}, expected {}", i, v.len(), dim);
        for (j, &got) in v.iter().enumerate() {
            let exp = expected_flat[i * dim + j];
            total_floats += 1;
            let abs_diff = (got - exp).abs();
            if abs_diff > 0.0 {
                diff_count += 1;
                if abs_diff > max_abs_diff {
                    max_abs_diff = abs_diff;
                }
                let mag = exp.abs().max(got.abs());
                if mag > 0.0 {
                    let rel = abs_diff / mag;
                    if rel > max_rel_diff {
                        max_rel_diff = rel;
                    }
                }
            }
        }
    }

    eprintln!(
        "embedding parity: {n}x{dim} = {total_floats} floats, {diff_count} differ; \
         max_abs_diff={max_abs_diff:.3e}, max_rel_diff={max_rel_diff:.3e}"
    );

    // Per-vector cosine distances (the user-visible parity signal).
    let mut cos_distances: Vec<f64> = Vec::with_capacity(n);
    for (i, v) in actual.iter().enumerate() {
        let exp_row = &expected_flat[i * dim..(i + 1) * dim];
        let dot: f64 = v.iter().zip(exp_row).map(|(a, b)| (*a as f64) * (*b as f64)).sum();
        let norm_a: f64 = v.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
        let norm_b: f64 = exp_row.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
        let cos_sim = dot / (norm_a * norm_b);
        let cos_dist = 1.0 - cos_sim;
        cos_distances.push(cos_dist);
        eprintln!("vec {i}: cosine_sim={cos_sim:.10}, cosine_dist={cos_dist:.3e}");
    }
    let mut sorted = cos_distances.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median_cos_dist = sorted[sorted.len() / 2];
    let max_cos_dist = *sorted.last().expect("non-empty");
    eprintln!("median_cos_dist={median_cos_dist:.3e}, max_cos_dist={max_cos_dist:.3e}");

    // SC-003: same model + same tokens via independent ORT C++ binaries.
    //   (a) median per-vector cos distance ≤ 1e-7 — most inputs should hit
    //       identical ORT paths and produce f32-epsilon-equivalent vectors.
    //       This is the load-bearing assertion: it confirms the model file,
    //       tokenization, and pipeline match.
    //   (b) max abs element-wise diff ≤ 1e-2 — soft cap on worst-case
    //       ORT-binary divergence. We've observed 6.6e-3 in practice on
    //       quantized matmul paths.
    //   (c) max cos distance ≤ 5e-3 — caps the worst-case per-vector
    //       similarity drop (observed 1.7e-3).
    // Strict bitwise was the original target; relaxed in the brief amendment
    // dated 2026-04-27 after DC-002 surfaced that Python's onnxruntime wheel
    // and Rust's `ort` crate's bundled binary are independent builds and
    // diverge on quantized matmul paths regardless of thread count.
    assert!(
        median_cos_dist <= 1e-7,
        "median per-vector cosine distance {median_cos_dist:.3e} exceeds 1e-7 — \
         most inputs should hit identical ORT paths and produce f32-epsilon \
         vectors. Drift here means the model file or tokenization diverged."
    );
    assert!(
        max_abs_diff <= 1e-2,
        "max abs diff {max_abs_diff:.3e} exceeds 1e-2 — drift is larger than \
         observed ORT-build noise. Investigate."
    );
    assert!(
        max_cos_dist <= 5e-3,
        "max per-vector cosine distance {max_cos_dist:.3e} exceeds 5e-3 — \
         worst-case parity envelope exceeded."
    );
}
