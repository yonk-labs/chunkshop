//! Integration tests for HttpSource. Spins up a tokio TCP listener with a
//! hand-rolled HTTP/1.1 response handler so we don't pull in a test-server
//! crate. Skips gracefully if port binding fails.

use chunkshop::config::HttpSourceConfig;
use chunkshop::source::HttpSource;

const HTML_PAGE: &str = "<!DOCTYPE html><html><head><title>Hello Page</title></head>\
                        <body>Hello world.</body></html>";
const PLAIN_TEXT: &str = "just plain text, no HTML, no title.";

/// Sitemap body with placeholder for the base URL — caller substitutes.
fn sitemap_xml(base: &str) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{base}/html</loc></url>
<url><loc>{base}/text</loc></url>
</urlset>"#
    )
}

/// Spawn a tiny HTTP/1.1 server on a random port. Returns the bound base URL.
/// Each connection is handled by a per-request task; the server runs until
/// `shutdown` (a oneshot) fires. Returns None if binding fails.
async fn spawn_test_server() -> Option<(String, tokio::sync::oneshot::Sender<()>)> {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.ok()?;
    let addr = listener.local_addr().ok()?;
    let base = format!("http://{addr}");
    let base_for_handler = base.clone();
    let (tx, mut rx) = tokio::sync::oneshot::channel::<()>();

    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = &mut rx => return,
                accept = listener.accept() => {
                    let Ok((stream, _)) = accept else { continue };
                    let base = base_for_handler.clone();
                    tokio::spawn(async move {
                        let _ = handle_connection(stream, base).await;
                    });
                }
            }
        }
    });

    Some((base, tx))
}

async fn handle_connection(
    mut stream: tokio::net::TcpStream,
    base: String,
) -> std::io::Result<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let mut buf = [0u8; 4096];
    let n = stream.read(&mut buf).await?;
    let req = String::from_utf8_lossy(&buf[..n]);
    // Parse the request line: METHOD PATH HTTP/1.1
    let path = req
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("/")
        .to_string();

    let (status_line, ctype, body): (&str, &str, String) = match path.as_str() {
        "/html" => (
            "HTTP/1.1 200 OK",
            "text/html; charset=utf-8",
            HTML_PAGE.to_string(),
        ),
        "/text" => (
            "HTTP/1.1 200 OK",
            "text/plain; charset=utf-8",
            PLAIN_TEXT.to_string(),
        ),
        "/sitemap.xml" => ("HTTP/1.1 200 OK", "application/xml", sitemap_xml(&base)),
        _ => (
            "HTTP/1.1 404 Not Found",
            "text/plain; charset=utf-8",
            "not found".to_string(),
        ),
    };

    let resp = format!(
        "{status_line}\r\nContent-Type: {ctype}\r\nContent-Length: {len}\r\nConnection: close\r\n\r\n{body}",
        len = body.len()
    );
    stream.write_all(resp.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn http_source_fetches_two_urls_with_correct_metadata() {
    let Some((base, shutdown)) = spawn_test_server().await else {
        eprintln!("skipping: can't bind local HTTP test server");
        return;
    };

    let cfg = HttpSourceConfig {
        urls: vec![format!("{base}/html"), format!("{base}/text")],
        sitemap: None,
    };
    let docs = HttpSource::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 2);

    let html = &docs[0];
    assert_eq!(html.id, format!("{base}/html"));
    assert_eq!(html.title.as_deref(), Some("Hello Page"));
    assert!(html.content.contains("Hello world."));
    assert_eq!(html.metadata["url"], html.id);
    assert_eq!(html.metadata["status_code"], 200);
    assert!(html.metadata["content_type"].as_str().unwrap().contains("text/html"));

    let text = &docs[1];
    assert_eq!(text.id, format!("{base}/text"));
    assert_eq!(text.title, None);
    assert_eq!(text.content, PLAIN_TEXT);
    assert!(text.metadata["content_type"].as_str().unwrap().contains("text/plain"));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn http_source_walks_sitemap() {
    let Some((base, shutdown)) = spawn_test_server().await else {
        eprintln!("skipping: can't bind local HTTP test server");
        return;
    };
    let cfg = HttpSourceConfig {
        urls: Vec::new(),
        sitemap: Some(format!("{base}/sitemap.xml")),
    };
    let docs = HttpSource::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 2);
    let ids: std::collections::HashSet<_> = docs.iter().map(|d| d.id.clone()).collect();
    assert!(ids.contains(&format!("{base}/html")));
    assert!(ids.contains(&format!("{base}/text")));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn http_source_dedups_urls_against_sitemap() {
    let Some((base, shutdown)) = spawn_test_server().await else {
        eprintln!("skipping: can't bind local HTTP test server");
        return;
    };
    let cfg = HttpSourceConfig {
        urls: vec![format!("{base}/html")],
        sitemap: Some(format!("{base}/sitemap.xml")),
    };
    let docs = HttpSource::new(cfg).iter_documents().await.expect("iter");
    assert_eq!(docs.len(), 2, "/html should appear once despite being in both lists");
    // First-occurrence ordering: /html came from urls; /text came from sitemap.
    assert_eq!(docs[0].id, format!("{base}/html"));
    assert_eq!(docs[1].id, format!("{base}/text"));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn http_source_errors_on_non_2xx() {
    let Some((base, shutdown)) = spawn_test_server().await else {
        eprintln!("skipping: can't bind local HTTP test server");
        return;
    };
    let cfg = HttpSourceConfig {
        urls: vec![format!("{base}/missing")],
        sitemap: None,
    };
    let err = HttpSource::new(cfg).iter_documents().await.unwrap_err().to_string();
    assert!(err.contains("404") || err.contains("status"), "got: {err}");

    let _ = shutdown.send(());
}
