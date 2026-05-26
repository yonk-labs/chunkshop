//! RM-B Task 4: depth-crawl + ETag/Last-Modified cursor + robots.txt + max_pages.
//!
//! Subset of `python/tests/chunkshop/test_http_crawl.py`'s scenarios. Tests
//! run against a stateful in-process HTTP/1.1 server (no wiremock dep) that
//! knows about ETags, conditional GETs, and robots.txt.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use chunkshop::config::HttpSourceConfig;
use chunkshop::sources::base::IncrementalSource;
use chunkshop::sources::http::HttpUrlCursor;
use chunkshop::sources::HttpSource;

#[derive(Clone)]
struct Route {
    status: u16,
    content_type: String,
    body: String,
    etag: Option<String>,
    last_modified: Option<String>,
}

#[derive(Clone, Default)]
struct ServerState {
    routes: HashMap<String, Route>,
    /// `GET /<path>` counter, for asserting cache-hit / cursor-skip behavior.
    requests: HashMap<String, u64>,
}

impl ServerState {
    fn add_html(&mut self, path: &str, etag: &str, body: &str) {
        self.routes.insert(
            path.to_string(),
            Route {
                status: 200,
                content_type: "text/html; charset=utf-8".to_string(),
                body: body.to_string(),
                etag: Some(format!("\"{etag}\"")),
                last_modified: Some("Mon, 25 May 2026 12:00:00 GMT".to_string()),
            },
        );
    }
    fn add_text(&mut self, path: &str, etag: &str, body: &str) {
        self.routes.insert(
            path.to_string(),
            Route {
                status: 200,
                content_type: "text/plain; charset=utf-8".to_string(),
                body: body.to_string(),
                etag: Some(format!("\"{etag}\"")),
                last_modified: None,
            },
        );
    }
    fn add_robots(&mut self, body: &str) {
        self.routes.insert(
            "/robots.txt".to_string(),
            Route {
                status: 200,
                content_type: "text/plain".to_string(),
                body: body.to_string(),
                etag: None,
                last_modified: None,
            },
        );
    }
}

async fn spawn_server(
    state: Arc<Mutex<ServerState>>,
) -> Option<(String, tokio::sync::oneshot::Sender<()>)> {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.ok()?;
    let addr = listener.local_addr().ok()?;
    let base = format!("http://{addr}");
    let (tx, mut rx) = tokio::sync::oneshot::channel::<()>();

    tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = &mut rx => return,
                accept = listener.accept() => {
                    let Ok((stream, _)) = accept else { continue };
                    let state = state.clone();
                    tokio::spawn(async move {
                        let _ = handle_conn(stream, state).await;
                    });
                }
            }
        }
    });
    Some((base, tx))
}

async fn handle_conn(
    mut stream: tokio::net::TcpStream,
    state: Arc<Mutex<ServerState>>,
) -> std::io::Result<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let mut buf = vec![0u8; 8192];
    let n = stream.read(&mut buf).await?;
    let req = String::from_utf8_lossy(&buf[..n]).to_string();

    // Parse request line + headers.
    let mut lines = req.split("\r\n");
    let first = lines.next().unwrap_or("");
    let mut parts = first.split_whitespace();
    let _method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/").to_string();

    let mut if_none_match: Option<String> = None;
    let mut if_modified_since: Option<String> = None;
    for line in lines {
        if line.is_empty() {
            break;
        }
        if let Some((k, v)) = line.split_once(": ") {
            let k_lower = k.to_ascii_lowercase();
            if k_lower == "if-none-match" {
                if_none_match = Some(v.trim().to_string());
            } else if k_lower == "if-modified-since" {
                if_modified_since = Some(v.trim().to_string());
            }
        }
    }

    let route = {
        let mut s = state.lock().unwrap();
        *s.requests.entry(path.clone()).or_insert(0) += 1;
        s.routes.get(&path).cloned()
    };

    let resp_bytes = match route {
        None => {
            b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found"
                .to_vec()
        }
        Some(r) => {
            // 304 if If-None-Match matches the route's ETag.
            let etag_matches = match (&if_none_match, &r.etag) {
                (Some(inm), Some(et)) => inm.trim() == et.trim(),
                _ => false,
            };
            let ims_matches = match (&if_modified_since, &r.last_modified) {
                (Some(ims), Some(lm)) => ims.trim() == lm.trim(),
                _ => false,
            };
            if etag_matches || ims_matches {
                let mut hdrs = String::new();
                if let Some(e) = &r.etag {
                    hdrs.push_str(&format!("ETag: {e}\r\n"));
                }
                format!("HTTP/1.1 304 Not Modified\r\n{hdrs}Connection: close\r\n\r\n").into_bytes()
            } else {
                let mut hdrs = format!(
                    "HTTP/1.1 {status} OK\r\nContent-Type: {ctype}\r\nContent-Length: {len}\r\n",
                    status = r.status,
                    ctype = r.content_type,
                    len = r.body.len(),
                );
                if let Some(e) = &r.etag {
                    hdrs.push_str(&format!("ETag: {e}\r\n"));
                }
                if let Some(lm) = &r.last_modified {
                    hdrs.push_str(&format!("Last-Modified: {lm}\r\n"));
                }
                hdrs.push_str("Connection: close\r\n\r\n");
                let mut bytes = hdrs.into_bytes();
                bytes.extend_from_slice(r.body.as_bytes());
                bytes
            }
        }
    };
    stream.write_all(&resp_bytes).await?;
    stream.shutdown().await?;
    Ok(())
}

fn fast_cfg(urls: Vec<String>) -> HttpSourceConfig {
    HttpSourceConfig {
        urls,
        sitemap: None,
        request_delay_seconds: 0.0,
        respect_robots: false,
        ..Default::default()
    }
}

#[tokio::test]
async fn crawl_depth_1_follows_links_same_host() {
    let mut s = ServerState::default();
    s.add_html(
        "/seed",
        "v1",
        r#"<html><body><a href="/page1">P1</a><a href="/page2">P2</a></body></html>"#,
    );
    s.add_html("/page1", "p1v1", r#"<html><body>page1</body></html>"#);
    s.add_html("/page2", "p2v1", r#"<html><body>page2</body></html>"#);
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state.clone()).await else {
        return;
    };

    let mut cfg = fast_cfg(vec![format!("{base}/seed")]);
    cfg.crawl_depth = 1;
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    let ids: Vec<String> = docs.iter().map(|d| d.id.clone()).collect();
    assert_eq!(ids.len(), 3, "seed + 2 linked pages, got {ids:?}");
    assert!(ids.iter().any(|i| i.ends_with("/seed")));
    assert!(ids.iter().any(|i| i.ends_with("/page1")));
    assert!(ids.iter().any(|i| i.ends_with("/page2")));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn crawl_depth_0_does_not_follow_links() {
    let mut s = ServerState::default();
    s.add_html(
        "/seed",
        "v1",
        r#"<html><body><a href="/page1">P1</a></body></html>"#,
    );
    s.add_html("/page1", "p1v1", r#"<html><body>page1</body></html>"#);
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state).await else {
        return;
    };

    let cfg = fast_cfg(vec![format!("{base}/seed")]);
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    assert_eq!(docs.len(), 1, "no crawl_depth → only seed fetched");
    assert!(docs[0].id.ends_with("/seed"));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn crawl_respects_max_pages() {
    let mut s = ServerState::default();
    s.add_html(
        "/seed",
        "v",
        r#"<html><body><a href="/a">A</a><a href="/b">B</a><a href="/c">C</a></body></html>"#,
    );
    s.add_html("/a", "a", "<p>A</p>");
    s.add_html("/b", "b", "<p>B</p>");
    s.add_html("/c", "c", "<p>C</p>");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state).await else {
        return;
    };

    let mut cfg = fast_cfg(vec![format!("{base}/seed")]);
    cfg.crawl_depth = 1;
    cfg.max_pages = 2;
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    assert_eq!(
        docs.len(),
        2,
        "max_pages caps emission: got {} docs",
        docs.len()
    );

    let _ = shutdown.send(());
}

#[tokio::test]
async fn cursor_skips_unchanged_etag_via_304() {
    let mut s = ServerState::default();
    s.add_text("/a", "v1", "hello a");
    s.add_text("/b", "v1", "hello b");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state.clone()).await else {
        return;
    };

    let cfg = fast_cfg(vec![format!("{base}/a"), format!("{base}/b")]);
    let src = HttpSource::new(cfg);

    // First sync: emit both, fingerprints carry etags.
    let cursor0 = src.empty_cursor();
    let first = src.iter_changes_since(&cursor0).await.unwrap();
    assert_eq!(first.len(), 2);
    assert_eq!(first[0].fingerprint.as_deref(), Some("\"v1\""));

    // Build the next cursor via cursor_from merge.
    let mut cursor1 = cursor0.clone();
    for d in &first {
        cursor1.extend(src.cursor_from(d));
    }
    assert_eq!(cursor1.len(), 2);

    // Second sync — server returns 304 for both → no docs emitted.
    let second = src.iter_changes_since(&cursor1).await.unwrap();
    assert!(
        second.is_empty(),
        "unchanged sync should be empty: {second:?}"
    );

    // Change /b's etag — only /b should re-emit.
    {
        let mut s = state.lock().unwrap();
        s.add_text("/b", "v2", "hello b updated");
    }
    let third = src.iter_changes_since(&cursor1).await.unwrap();
    assert_eq!(third.len(), 1);
    let id = &third[0].id;
    assert!(id.ends_with("/b"), "only /b changed; got {id}");
    assert_eq!(third[0].fingerprint.as_deref(), Some("\"v2\""));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn cursor_from_returns_per_url_delta() {
    let mut s = ServerState::default();
    s.add_text("/a", "etag-a", "body a");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state).await else {
        return;
    };

    let cfg = fast_cfg(vec![format!("{base}/a")]);
    let src = HttpSource::new(cfg);
    let docs = src.iter_changes_since(&src.empty_cursor()).await.unwrap();
    assert_eq!(docs.len(), 1);
    let delta = src.cursor_from(&docs[0]);
    assert_eq!(delta.len(), 1, "delta is a single-key map");
    let entry: &HttpUrlCursor = delta.values().next().unwrap();
    assert_eq!(entry.etag.as_deref(), Some("\"etag-a\""));

    let _ = shutdown.send(());
}

#[tokio::test]
async fn robots_txt_disallow_skips_url() {
    let mut s = ServerState::default();
    // Disallow /private (more specific than /), but allow /public.
    s.add_robots("User-agent: *\nDisallow: /private\nAllow: /public\n");
    s.add_text("/public", "pub", "hello public");
    s.add_text("/private", "pri", "hello private");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state.clone()).await else {
        return;
    };

    let cfg = HttpSourceConfig {
        urls: vec![format!("{base}/public"), format!("{base}/private")],
        sitemap: None,
        request_delay_seconds: 0.0,
        respect_robots: true,
        ..Default::default()
    };
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    let ids: Vec<String> = docs.iter().map(|d| d.id.clone()).collect();
    assert_eq!(
        ids.len(),
        1,
        "robots.txt should block /private; got {ids:?}"
    );
    assert!(ids[0].ends_with("/public"));

    // robots.txt was fetched.
    let robots_fetches = state
        .lock()
        .unwrap()
        .requests
        .get("/robots.txt")
        .copied()
        .unwrap_or(0);
    assert!(
        robots_fetches >= 1,
        "robots.txt must be fetched at least once"
    );

    let _ = shutdown.send(());
}

#[tokio::test]
async fn off_host_links_filtered_by_default() {
    let mut s = ServerState::default();
    // Page links to an external host. The external host isn't on our test
    // server — but the filter should reject the link before fetching it,
    // so no error is raised.
    s.add_html(
        "/seed",
        "v",
        r#"<html><body><a href="http://other.example.test/page">X</a><a href="/local">L</a></body></html>"#,
    );
    s.add_html("/local", "l", "<p>local</p>");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state).await else {
        return;
    };

    let mut cfg = fast_cfg(vec![format!("{base}/seed")]);
    cfg.crawl_depth = 1;
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    let ids: Vec<String> = docs.iter().map(|d| d.id.clone()).collect();
    assert!(
        ids.iter().any(|i| i.ends_with("/local")),
        "same-host link followed: {ids:?}"
    );
    assert!(
        !ids.iter().any(|i| i.contains("other.example.test")),
        "off-host link not followed by default: {ids:?}"
    );

    let _ = shutdown.send(());
}

#[tokio::test]
async fn url_dedup_via_normalization() {
    let mut s = ServerState::default();
    s.add_text("/a", "v", "hello");
    let state = Arc::new(Mutex::new(s));
    let Some((base, shutdown)) = spawn_server(state).await else {
        return;
    };

    // Two URLs that should normalize to the same identity.
    let cfg = fast_cfg(vec![format!("{base}/a"), format!("{base}/a#section")]);
    let docs = HttpSource::new(cfg).iter_documents().await.unwrap();
    assert_eq!(docs.len(), 1, "fragment-only difference must dedup");

    let _ = shutdown.send(());
}
