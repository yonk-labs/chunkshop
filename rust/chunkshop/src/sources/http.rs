//! HTTP source with depth-bounded crawl + ETag/Last-Modified incremental sync.
//!
//! Mirrors `python/src/chunkshop/sources/http.py` (RM-B Task 4 / Python
//! commit `fcbad65`). Behavior summary:
//!
//! - `crawl_depth = 0` (default): fetch only the listed URLs (+ sitemap).
//! - `crawl_depth >= 1`: BFS crawl from seeds. Same-host filter is on by
//!   default; flip `allow_external` to follow off-host links.
//! - Conditional GETs via `If-None-Match` / `If-Modified-Since`. 304 → skip.
//! - Cursor: `{ url: { etag, last_modified } }`. Per-doc delta returned by
//!   `cursor_from`; consumer merges deltas into running cursor (same shape
//!   as the S3 source).
//! - Polite delay between requests; robots.txt enforcement (one fetch per
//!   host, cached).
//! - Non-2xx responses are logged-and-skipped, not raised (Python parity).

use anyhow::{Context, Result};
use regex::Regex;
use reqwest::header::{HeaderMap, HeaderValue, CONTENT_TYPE, ETAG, LAST_MODIFIED};
use reqwest::StatusCode;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::future::Future;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::config::HttpSourceConfig;
use crate::sources::base::{Document, IncrementalSource};

/// Per-URL cursor entry for `HttpSource::iter_changes_since`.
///
/// `etag` is sent as `If-None-Match`; `last_modified` as `If-Modified-Since`.
/// A 304 Not Modified response skips emitting the doc.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct HttpUrlCursor {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub etag: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_modified: Option<String>,
}

/// MIME types we treat as fetchable text. Everything else is skipped with a
/// warning (mirrors Python `_TEXTY_MIMES`).
const TEXTY_MIMES: &[&str] = &[
    "text/html",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
];

/// Minimal `robots.txt` view used for `can_fetch` checks.
#[derive(Debug, Default, Clone)]
struct RobotsRules {
    disallow: Vec<String>,
    allow: Vec<String>,
}

impl RobotsRules {
    fn parse(body: &str, ua: &str) -> Self {
        let mut groups: HashMap<String, RobotsRules> = HashMap::new();
        let mut current_uas: Vec<String> = Vec::new();
        for raw_line in body.lines() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (key, value) = match line.split_once(':') {
                Some((k, v)) => (k.trim().to_lowercase(), v.trim().to_string()),
                None => continue,
            };
            let value = value.split('#').next().unwrap_or("").trim().to_string();
            match key.as_str() {
                "user-agent" => {
                    current_uas.push(value.to_lowercase());
                }
                "disallow" => {
                    for u in &current_uas {
                        groups
                            .entry(u.clone())
                            .or_default()
                            .disallow
                            .push(value.clone());
                    }
                }
                "allow" => {
                    for u in &current_uas {
                        groups.entry(u.clone()).or_default().allow.push(value.clone());
                    }
                }
                _ => {}
            }
        }
        let ua_lower = ua.to_lowercase();
        let mut best: Option<&RobotsRules> = None;
        for (rules_ua, rules) in &groups {
            if rules_ua == "*" {
                best.get_or_insert(rules);
            } else if ua_lower.contains(rules_ua.as_str()) {
                best = Some(rules);
            }
        }
        best.cloned().unwrap_or_default()
    }

    fn can_fetch(&self, path: &str) -> bool {
        let allow_match = self
            .allow
            .iter()
            .filter(|p| !p.is_empty() && path.starts_with(p.as_str()))
            .map(|p| p.len())
            .max()
            .unwrap_or(0);
        let disallow_match = self
            .disallow
            .iter()
            .filter(|p| !p.is_empty() && path.starts_with(p.as_str()))
            .map(|p| p.len())
            .max()
            .unwrap_or(0);
        if disallow_match == 0 {
            return true;
        }
        allow_match >= disallow_match
    }
}

pub struct HttpSource {
    cfg: HttpSourceConfig,
    last_request_ts: Mutex<Option<Instant>>,
    robots_cache: Mutex<HashMap<String, Option<RobotsRules>>>,
}

impl HttpSource {
    pub fn new(cfg: HttpSourceConfig) -> Self {
        Self {
            cfg,
            last_request_ts: Mutex::new(None),
            robots_cache: Mutex::new(HashMap::new()),
        }
    }

    fn build_client(&self) -> Result<reqwest::Client> {
        let mut headers = HeaderMap::new();
        if let Ok(ua) = HeaderValue::from_str(&self.cfg.user_agent) {
            headers.insert(reqwest::header::USER_AGENT, ua);
        }
        reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::default())
            .build()
            .context("build reqwest client")
    }

    async fn polite_wait(&self) {
        let delay = self.cfg.request_delay_seconds;
        if delay <= 0.0 {
            return;
        }
        let wait = {
            let mut guard = self.last_request_ts.lock().unwrap();
            let now = Instant::now();
            let wait = match *guard {
                Some(last) => {
                    let elapsed = now.duration_since(last).as_secs_f64();
                    if elapsed < delay {
                        Some(Duration::from_secs_f64(delay - elapsed))
                    } else {
                        None
                    }
                }
                None => None,
            };
            *guard = Some(Instant::now());
            wait
        };
        if let Some(d) = wait {
            tokio::time::sleep(d).await;
            *self.last_request_ts.lock().unwrap() = Some(Instant::now());
        }
    }

    async fn robots_for(&self, client: &reqwest::Client, url: &str) -> Option<RobotsRules> {
        if !self.cfg.respect_robots {
            return None;
        }
        let parsed = reqwest::Url::parse(url).ok()?;
        let host = parsed.host_str()?;
        let port_part = parsed.port().map(|p| format!(":{p}")).unwrap_or_default();
        let host_key = format!("{}://{}{}", parsed.scheme(), host, port_part);

        {
            let cache = self.robots_cache.lock().unwrap();
            if let Some(cached) = cache.get(&host_key) {
                return cached.clone();
            }
        }

        let robots_url = format!("{host_key}/robots.txt");
        let fetched = self.fetch_robots_body(client, &robots_url).await;
        let parsed_rules = fetched.map(|body| RobotsRules::parse(&body, &self.cfg.user_agent));

        self.robots_cache
            .lock()
            .unwrap()
            .insert(host_key, parsed_rules.clone());
        parsed_rules
    }

    async fn fetch_robots_body(&self, client: &reqwest::Client, url: &str) -> Option<String> {
        self.polite_wait().await;
        let resp = client.get(url).send().await.ok()?;
        if !resp.status().is_success() {
            return None;
        }
        resp.text().await.ok()
    }

    fn robots_allows(rules: &Option<RobotsRules>, url: &str) -> bool {
        match rules {
            None => true,
            Some(r) => match reqwest::Url::parse(url) {
                Ok(u) => r.can_fetch(u.path()),
                Err(_) => true,
            },
        }
    }

    async fn request(
        &self,
        client: &reqwest::Client,
        url: &str,
        cursor_entry: Option<&HttpUrlCursor>,
    ) -> Option<(StatusCode, HeaderMap, String)> {
        self.polite_wait().await;
        let mut req = client.get(url);
        if let Some(ce) = cursor_entry {
            if let Some(etag) = &ce.etag {
                if let Ok(v) = HeaderValue::from_str(etag) {
                    req = req.header(reqwest::header::IF_NONE_MATCH, v);
                }
            }
            if let Some(lm) = &ce.last_modified {
                if let Ok(v) = HeaderValue::from_str(lm) {
                    req = req.header(reqwest::header::IF_MODIFIED_SINCE, v);
                }
            }
        }
        let resp = match req.send().await {
            Ok(r) => r,
            Err(err) => {
                tracing::warn!(target: "chunkshop::http", "GET {url} failed: {err}");
                return None;
            }
        };
        let status = resp.status();
        let headers = resp.headers().clone();
        let body = match resp.text().await {
            Ok(t) => t,
            Err(err) => {
                tracing::warn!(target: "chunkshop::http", "reading body of {url}: {err}");
                return None;
            }
        };
        Some((status, headers, body))
    }

    async fn fetch_one(
        &self,
        client: &reqwest::Client,
        url: &str,
        cursor_entry: Option<&HttpUrlCursor>,
        robots: &Option<RobotsRules>,
    ) -> (Option<Document>, Vec<String>) {
        if !Self::robots_allows(robots, url) {
            tracing::info!(target: "chunkshop::http", "robots.txt disallows {url}; skipping");
            return (None, Vec::new());
        }
        let (status, headers, body) = match self.request(client, url, cursor_entry).await {
            Some(t) => t,
            None => return (None, Vec::new()),
        };
        if status == StatusCode::NOT_MODIFIED {
            return (None, Vec::new());
        }
        if !status.is_success() {
            tracing::warn!(target: "chunkshop::http", "GET {url}: status {status}; skipping");
            return (None, Vec::new());
        }
        let ctype_full = headers
            .get(CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let mime = content_type_root(&ctype_full);
        let etag = headers
            .get(ETAG)
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());
        let last_modified = headers
            .get(LAST_MODIFIED)
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());

        if !mime.starts_with("text/") && !TEXTY_MIMES.contains(&mime.as_str()) {
            tracing::warn!(
                target: "chunkshop::http",
                "Skipping binary content {mime} for {url} (use the files source for binaries)"
            );
            return (None, Vec::new());
        }

        let (content, title, links) = if mime == "text/html" {
            let title = extract_title(&body);
            let links = extract_links(&body, url);
            let stripped = strip_html_to_text(&body);
            (stripped, title, links)
        } else {
            (body, None, Vec::new())
        };

        let metadata = serde_json::json!({
            "url": url,
            "status_code": status.as_u16(),
            "content_type": ctype_full,
            "etag": etag,
            "last_modified": last_modified,
        });
        let doc = Document {
            id: url.to_string(),
            content,
            title,
            metadata,
            fingerprint: etag,
        };
        (Some(doc), links)
    }

    async fn seed_urls(&self, client: &reqwest::Client) -> Vec<String> {
        let mut seen: HashSet<String> = HashSet::new();
        let mut out: Vec<String> = Vec::new();
        for u in &self.cfg.urls {
            let n = normalize_url(u);
            if seen.insert(n) {
                out.push(u.clone());
            }
        }
        if let Some(sm) = &self.cfg.sitemap {
            if let Some((status, _, body)) = self.request(client, sm, None).await {
                if status.is_success() {
                    for u in parse_sitemap(&body) {
                        let n = normalize_url(&u);
                        if seen.insert(n) {
                            out.push(u);
                        }
                    }
                } else {
                    tracing::warn!(target: "chunkshop::http", "sitemap fetch {sm} status {status}");
                }
            } else {
                tracing::warn!(target: "chunkshop::http", "sitemap fetch {sm} failed");
            }
        }
        out
    }

    async fn crawl(&self, cursor: &BTreeMap<String, HttpUrlCursor>) -> Result<Vec<Document>> {
        let client = self.build_client()?;
        let seeds = self.seed_urls(&client).await;
        let seed_hosts: HashSet<String> = seeds
            .iter()
            .filter_map(|s| reqwest::Url::parse(s).ok())
            .filter_map(|u| u.host_str().map(|h| h.to_lowercase()))
            .collect();

        let mut visited: HashSet<String> = HashSet::new();
        let mut frontier: VecDeque<(String, u32)> = VecDeque::new();
        for s in &seeds {
            frontier.push_back((s.clone(), self.cfg.crawl_depth));
        }
        let mut emitted: u64 = 0;
        let mut out: Vec<Document> = Vec::new();

        while let Some((url, depth_left)) = frontier.pop_front() {
            if emitted >= self.cfg.max_pages {
                break;
            }
            let norm = normalize_url(&url);
            if !visited.insert(norm.clone()) {
                continue;
            }
            if !self.cfg.allow_external {
                if let Some(host) = reqwest::Url::parse(&url)
                    .ok()
                    .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
                {
                    if !seed_hosts.contains(&host) {
                        continue;
                    }
                }
            }

            let robots = self.robots_for(&client, &url).await;
            let ce = cursor.get(&url).cloned();
            let (doc, links) = self.fetch_one(&client, &url, ce.as_ref(), &robots).await;
            if let Some(d) = doc {
                emitted += 1;
                out.push(d);
                if emitted >= self.cfg.max_pages {
                    break;
                }
            }
            if depth_left > 0 && !links.is_empty() {
                for link in links {
                    let ln = normalize_url(&link);
                    if visited.contains(&ln) {
                        continue;
                    }
                    if !self.cfg.allow_external {
                        if let (Some(lh), Some(uh)) = (host_of(&link), host_of(&url)) {
                            if !seed_hosts.contains(&lh) && lh != uh {
                                continue;
                            }
                        }
                    }
                    frontier.push_back((link, depth_left - 1));
                }
            }
        }
        Ok(out)
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let empty = BTreeMap::new();
        self.crawl(&empty).await
    }
}

impl IncrementalSource for HttpSource {
    type Cursor = BTreeMap<String, HttpUrlCursor>;

    fn empty_cursor(&self) -> Self::Cursor {
        BTreeMap::new()
    }

    fn iter_changes_since(
        &self,
        cursor: &Self::Cursor,
    ) -> impl Future<Output = Result<Vec<Document>>> + Send {
        let cursor = cursor.clone();
        async move { self.crawl(&cursor).await }
    }

    fn cursor_from(&self, last_document: &Document) -> Self::Cursor {
        let url = last_document
            .metadata
            .get("url")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| last_document.id.clone());
        let etag = last_document.fingerprint.clone();
        let last_modified = last_document
            .metadata
            .get("last_modified")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let mut delta = BTreeMap::new();
        delta.insert(url, HttpUrlCursor { etag, last_modified });
        delta
    }
}

// ----- helpers -----------------------------------------------------------

fn normalize_url(url: &str) -> String {
    match reqwest::Url::parse(url) {
        Ok(mut u) => {
            u.set_fragment(None);
            if u.path().is_empty() {
                let _ = u.set_path("/");
            }
            let scheme = u.scheme().to_lowercase();
            let host = u.host_str().map(|s| s.to_lowercase()).unwrap_or_default();
            let port = u.port().map(|p| format!(":{p}")).unwrap_or_default();
            let path = u.path();
            let query = u.query().map(|q| format!("?{q}")).unwrap_or_default();
            format!("{scheme}://{host}{port}{path}{query}")
        }
        Err(_) => url.to_string(),
    }
}

fn host_of(url: &str) -> Option<String> {
    reqwest::Url::parse(url)
        .ok()
        .and_then(|u| u.host_str().map(|h| h.to_lowercase()))
}

fn content_type_root(ctype: &str) -> String {
    ctype.split(';').next().unwrap_or("").trim().to_lowercase()
}

fn extract_title(body: &str) -> Option<String> {
    let re = Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
    let captures = re.captures(body)?;
    let raw = captures.get(1)?.as_str().trim();
    if raw.is_empty() {
        None
    } else {
        Some(raw.to_string())
    }
}

fn strip_html_to_text(body: &str) -> String {
    let no_script = Regex::new(r"(?is)<script[^>]*>.*?</script>")
        .map(|r| r.replace_all(body, "").to_string())
        .unwrap_or_else(|_| body.to_string());
    let no_style = Regex::new(r"(?is)<style[^>]*>.*?</style>")
        .map(|r| r.replace_all(&no_script, "").to_string())
        .unwrap_or(no_script);
    let no_tags = Regex::new(r"(?is)<[^>]+>")
        .map(|r| r.replace_all(&no_style, " ").to_string())
        .unwrap_or(no_style);
    no_tags.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn extract_links(body: &str, page_url: &str) -> Vec<String> {
    let re = match Regex::new(r#"(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']"#) {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };
    let base = match reqwest::Url::parse(page_url) {
        Ok(b) => b,
        Err(_) => return Vec::new(),
    };
    let mut out = Vec::new();
    for caps in re.captures_iter(body) {
        let href = caps.get(1).map(|m| m.as_str().trim()).unwrap_or("");
        if href.is_empty() {
            continue;
        }
        let lower = href.to_lowercase();
        if lower.starts_with("mailto:")
            || lower.starts_with("javascript:")
            || lower.starts_with("tel:")
            || lower.starts_with('#')
        {
            continue;
        }
        if let Ok(resolved) = base.join(href) {
            out.push(resolved.to_string());
        }
    }
    out
}

fn parse_sitemap(body: &str) -> Vec<String> {
    let re = match Regex::new(r"(?is)<loc>(.*?)</loc>") {
        Ok(r) => r,
        Err(_) => return Vec::new(),
    };
    re.captures_iter(body)
        .filter_map(|c| c.get(1).map(|m| m.as_str().trim().to_string()))
        .filter(|s| !s.is_empty())
        .collect()
}
