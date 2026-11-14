//! RSD-10: redirects are resolved hop by hop. Every hop must pass the scheme,
//! host and robots rules; at most 5 hops; `example.com` and `www.example.com`
//! are one host; the document id stays the originally requested URL.
//!
//! The server listens on 127.0.0.1 and (when available) [::1] on the same
//! port, so `127.0.0.1`, `localhost` and `www.localhost` are three hostnames
//! that all reach it.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use chunkshop::config::HttpSourceConfig;
use chunkshop::sources::HttpSource;

#[derive(Clone)]
enum Resp {
    Redirect(u16, String),
    Ok(String),
}

#[derive(Default)]
struct State {
    routes: HashMap<String, Resp>,
    hits: HashMap<String, u64>,
}

type Shared = Arc<Mutex<State>>;

fn spawn_loop(listener: tokio::net::TcpListener, state: Shared) {
    tokio::spawn(async move {
        loop {
            let Ok((mut stream, _)) = listener.accept().await else { continue };
            let state = state.clone();
            tokio::spawn(async move {
                use tokio::io::{AsyncReadExt, AsyncWriteExt};
                let mut buf = vec![0u8; 8192];
                let Ok(n) = stream.read(&mut buf).await else { return };
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let path = req.split_whitespace().nth(1).unwrap_or("/").to_string();
                let resp = {
                    let mut s = state.lock().unwrap();
                    *s.hits.entry(path.clone()).or_insert(0) += 1;
                    s.routes.get(&path).cloned()
                };
                let bytes = match resp {
                    None => "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        .to_string(),
                    Some(Resp::Redirect(code, loc)) => format!(
                        "HTTP/1.1 {code} Redirect\r\nLocation: {loc}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    ),
                    Some(Resp::Ok(body)) => format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    ),
                };
                let _ = stream.write_all(bytes.as_bytes()).await;
                let _ = stream.shutdown().await;
            });
        }
    });
}

/// Returns (port, state, v6_available).
async fn server(routes: Vec<(&str, Resp)>) -> (u16, Shared, bool) {
    let state: Shared = Arc::new(Mutex::new(State {
        routes: routes.into_iter().map(|(p, r)| (p.to_string(), r)).collect(),
        hits: HashMap::new(),
    }));
    let v4 = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = v4.local_addr().unwrap().port();
    spawn_loop(v4, state.clone());
    let v6 = match tokio::net::TcpListener::bind(format!("[::1]:{port}")).await {
        Ok(l) => {
            spawn_loop(l, state.clone());
            true
        }
        Err(_) => false,
    };
    (port, state, v6)
}

fn cfg(urls: Vec<String>, robots: bool) -> HttpSourceConfig {
    HttpSourceConfig {
        urls,
        sitemap: None,
        request_delay_seconds: 0.0,
        respect_robots: robots,
        ..Default::default()
    }
}

fn hits(s: &Shared, path: &str) -> u64 {
    s.lock().unwrap().hits.get(path).copied().unwrap_or(0)
}

#[tokio::test]
async fn off_host_redirect_is_refused_and_never_requested() {
    let (p, st, _) = server(vec![]).await;
    st.lock().unwrap().routes.insert(
        "/start".into(),
        Resp::Redirect(302, format!("http://localhost:{p}/secret")),
    );
    st.lock().unwrap().routes.insert("/secret".into(), Resp::Ok("TOP SECRET".into()));
    let docs = HttpSource::new(cfg(vec![format!("http://127.0.0.1:{p}/start")], false))
        .iter_documents()
        .await
        .unwrap();
    assert!(docs.is_empty(), "no document for a refused redirect: {docs:?}");
    assert_eq!(hits(&st, "/secret"), 0, "redirect target must never be requested");
}

#[tokio::test]
async fn same_host_redirect_is_followed_and_id_stays_requested_url() {
    let (p, st, _) = server(vec![]).await;
    st.lock().unwrap().routes.insert("/old".into(), Resp::Redirect(301, "/new".into()));
    st.lock().unwrap().routes.insert("/new".into(), Resp::Ok("fresh content".into()));
    let url = format!("http://127.0.0.1:{p}/old");
    let docs = HttpSource::new(cfg(vec![url.clone()], false)).iter_documents().await.unwrap();
    assert_eq!(docs.len(), 1);
    assert_eq!(docs[0].id, url);
    assert!(docs[0].content.contains("fresh content"));
}

#[tokio::test]
async fn apex_and_www_count_as_one_host_both_ways() {
    let (p, st, v6) = server(vec![]).await;
    if !v6 {
        eprintln!("skipping: [::1] unavailable (www.localhost needs it)");
        return;
    }
    {
        let mut s = st.lock().unwrap();
        s.routes.insert("/a".into(), Resp::Redirect(302, format!("http://www.localhost:{p}/b")));
        s.routes.insert("/b".into(), Resp::Ok("on www".into()));
        s.routes.insert("/c".into(), Resp::Redirect(302, format!("http://localhost:{p}/d")));
        s.routes.insert("/d".into(), Resp::Ok("on apex".into()));
    }
    let urls = vec![format!("http://localhost:{p}/a"), format!("http://www.localhost:{p}/c")];
    let docs = HttpSource::new(cfg(urls, false)).iter_documents().await.unwrap();
    let mut got: Vec<String> = docs.iter().map(|d| d.content.clone()).collect();
    got.sort();
    assert_eq!(got, vec!["on apex".to_string(), "on www".to_string()]);
}

#[tokio::test]
async fn www_lookalike_host_is_refused() {
    let (p, st, v6) = server(vec![]).await;
    if !v6 {
        return;
    }
    st.lock().unwrap().routes.insert(
        "/x".into(),
        Resp::Redirect(302, format!("http://www.localhost.evil.test:{p}/y")),
    );
    let docs = HttpSource::new(cfg(vec![format!("http://www.localhost:{p}/x")], false))
        .iter_documents()
        .await
        .unwrap();
    assert!(docs.is_empty(), "lookalike host must not match: {docs:?}");
}

#[tokio::test]
async fn redirect_limit_is_five_hops_and_loops_terminate() {
    let (p, st, _) = server(vec![]).await;
    {
        let mut s = st.lock().unwrap();
        // chain5: 5 redirects then 200 (allowed). chain6: 6 redirects (refused).
        for i in 0..5 {
            s.routes.insert(format!("/c5/{i}"), Resp::Redirect(302, format!("/c5/{}", i + 1)));
        }
        s.routes.insert("/c5/5".into(), Resp::Ok("end of chain".into()));
        for i in 0..6 {
            s.routes.insert(format!("/c6/{i}"), Resp::Redirect(302, format!("/c6/{}", i + 1)));
        }
        s.routes.insert("/c6/6".into(), Resp::Ok("too far".into()));
        s.routes.insert("/loop".into(), Resp::Redirect(302, "/loop".into()));
    }
    let docs = HttpSource::new(cfg(vec![format!("http://127.0.0.1:{p}/c5/0")], false))
        .iter_documents()
        .await
        .unwrap();
    assert_eq!(docs.len(), 1, "5 hops are allowed");
    let docs = HttpSource::new(cfg(vec![format!("http://127.0.0.1:{p}/c6/0")], false))
        .iter_documents()
        .await
        .unwrap();
    assert!(docs.is_empty(), "6 hops must be refused");
    assert_eq!(hits(&st, "/c6/6"), 0);
    let docs = HttpSource::new(cfg(vec![format!("http://127.0.0.1:{p}/loop")], false))
        .iter_documents()
        .await
        .unwrap();
    assert!(docs.is_empty());
    assert_eq!(hits(&st, "/loop"), 6, "loop is cut after 1 request + 5 hops");
}

#[tokio::test]
async fn redirect_to_robots_disallowed_path_is_refused() {
    let (p, st, _) = server(vec![]).await;
    {
        let mut s = st.lock().unwrap();
        s.routes.insert("/robots.txt".into(), Resp::Ok("User-agent: *\nDisallow: /private\n".into()));
        s.routes.insert("/go".into(), Resp::Redirect(302, "/private".into()));
        s.routes.insert("/private".into(), Resp::Ok("hidden".into()));
    }
    let docs = HttpSource::new(cfg(vec![format!("http://127.0.0.1:{p}/go")], true))
        .iter_documents()
        .await
        .unwrap();
    assert!(docs.is_empty(), "robots-disallowed hop target must be refused: {docs:?}");
    assert_eq!(hits(&st, "/private"), 0);
}
