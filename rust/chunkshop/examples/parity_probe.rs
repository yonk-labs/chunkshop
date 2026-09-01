//! Throwaway probe: does fastembed-rs agree with Python fastembed on the
//! CLIP text tower? Python applies NO pooling (the Qdrant ONNX model already
//! emits a pooled 512-d vector); fastembed-rs declares Pooling::Mean for
//! ClipVitB32. If those disagree, SC-12's bit-level parity is at risk.
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let fixture = "a game sprite or pixel art";
    let opts = InitOptions::new(EmbeddingModel::ClipVitB32).with_show_download_progress(false);
    let mut model = TextEmbedding::try_new(opts)?;
    let out = model.embed(vec![fixture], None)?;
    let v = &out[0];
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    println!("rust clip_text dim={} |v|={:.6}", v.len(), norm);
    println!(
        "rust first8 = {:?}",
        &v[..8.min(v.len())]
            .iter()
            .map(|x| (x * 1e8).round() / 1e8)
            .collect::<Vec<_>>()
    );
    println!("rust sum    = {:.8}", v.iter().sum::<f32>());
    Ok(())
}
