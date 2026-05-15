//! Integration test for S3Source. Skips cleanly when `CHUNKSHOP_S3_TEST_BUCKET`
//! is unset. When set (and the bucket reachable with the standard AWS
//! credentials), uploads a 3-key fixture, runs the source, asserts shape, and
//! cleans up. Set `CHUNKSHOP_S3_TEST_ENDPOINT` to point at a local minio (or
//! any S3-compatible) server instead of AWS.

use std::env;

use chunkshop::config::S3SourceConfig;
use chunkshop::sources::S3Source;

#[tokio::test]
async fn s3_source_lists_and_fetches_three_keys() {
    let bucket = match env::var("CHUNKSHOP_S3_TEST_BUCKET") {
        Ok(b) => b,
        Err(_) => {
            eprintln!("CHUNKSHOP_S3_TEST_BUCKET not set; skipping S3 source test");
            return;
        }
    };
    let endpoint_url = env::var("CHUNKSHOP_S3_TEST_ENDPOINT").ok();

    use object_store::aws::AmazonS3Builder;
    use object_store::{path::Path as ObjPath, ObjectStore, PutPayload};

    let mut builder = AmazonS3Builder::from_env().with_bucket_name(&bucket);
    if let Some(ep) = &endpoint_url {
        builder = builder.with_endpoint(ep).with_allow_http(ep.starts_with("http://"));
    }
    let store = match builder.build() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("skipping: can't build S3 client (creds?): {e:#}");
            return;
        }
    };

    let prefix = format!(
        "chunkshop-s3-test/{}/",
        uuid_like_suffix()
    );
    let keys = vec![
        format!("{prefix}alpha.txt"),
        format!("{prefix}bravo.txt"),
        format!("{prefix}charlie.txt"),
    ];
    let bodies = ["alpha body", "bravo body — longer", "charlie body"];

    // Upload fixture.
    for (key, body) in keys.iter().zip(bodies.iter()) {
        let path = ObjPath::from(key.clone());
        store
            .put(&path, PutPayload::from_bytes(body.as_bytes().to_vec().into()))
            .await
            .expect("put fixture");
    }

    // Run the source.
    let cfg = S3SourceConfig {
        bucket: bucket.clone(),
        prefix: prefix.clone(),
        endpoint_url: endpoint_url.clone(),
    };
    let docs = S3Source::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 3);

    let mut sorted = docs.iter().collect::<Vec<_>>();
    sorted.sort_by(|a, b| a.id.cmp(&b.id));
    for (d, (key, body)) in sorted.iter().zip(keys.iter().zip(bodies.iter())) {
        assert_eq!(d.id, format!("s3://{bucket}/{key}"));
        assert_eq!(d.content, *body);
        assert_eq!(d.title, None);
        assert_eq!(d.metadata["bucket"], bucket);
        assert_eq!(d.metadata["key"], *key);
        assert_eq!(d.metadata["size"].as_u64().unwrap() as usize, body.len());
        assert!(d.metadata["etag"].as_str().is_some());
    }

    // Cleanup.
    for key in &keys {
        let path = ObjPath::from(key.clone());
        let _ = store.delete(&path).await;
    }
}

/// Cheap unique-ish prefix for the test run. Not a real UUID — we don't want
/// a uuid dep just for tests. Uses a nanos timestamp + a short `process::id`.
fn uuid_like_suffix() -> String {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{ts:x}-{}", std::process::id())
}
