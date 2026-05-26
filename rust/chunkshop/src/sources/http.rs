//! HTTP source. Mirrors `python/src/chunkshop/sources/http.py`.

use anyhow::{anyhow, Context, Result};

use crate::config::HttpSourceConfig;
use crate::sources::base::Document;

pub struct HttpSource {
    cfg: HttpSourceConfig,
}

impl HttpSource {
    pub fn new(cfg: HttpSourceConfig) -> Self {
        Self { cfg }
    }

    async fn fetch(client: &reqwest::Client, url: &str) -> Result<(String, u16, String)> {
        let resp = client
            .get(url)
            .header("User-Agent", "chunkshop-http/1.0")
            .send()
            .await
            .with_context(|| format!("GET {url}"))?;
        let status = resp.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(anyhow!("GET {url}: status {status}"));
        }
        let ctype = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let body = resp
            .text()
            .await
            .with_context(|| format!("reading body of {url}"))?;
        Ok((body, status, ctype))
    }

    fn extract_title(body: &str) -> Option<String> {
        let re = regex::Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
        let captures = re.captures(body)?;
        let raw = captures.get(1)?.as_str().trim();
        if raw.is_empty() {
            None
        } else {
            Some(raw.to_string())
        }
    }

    fn parse_sitemap(body: &str) -> Vec<String> {
        let re = match regex::Regex::new(r"(?is)<loc>(.*?)</loc>") {
            Ok(r) => r,
            Err(_) => return Vec::new(),
        };
        re.captures_iter(body)
            .filter_map(|c| c.get(1).map(|m| m.as_str().trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect()
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut fetch_list: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for u in &self.cfg.urls {
            if seen.insert(u.clone()) {
                fetch_list.push(u.clone());
            }
        }
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .context("build reqwest client")?;
        if let Some(sm) = &self.cfg.sitemap {
            let (sm_body, _, _) = Self::fetch(&client, sm).await?;
            for u in Self::parse_sitemap(&sm_body) {
                if seen.insert(u.clone()) {
                    fetch_list.push(u);
                }
            }
        }

        let mut out: Vec<Document> = Vec::with_capacity(fetch_list.len());
        for url in fetch_list {
            let (body, status, ctype) = Self::fetch(&client, &url).await?;
            let title = Self::extract_title(&body);
            out.push(Document {
                id: url.clone(),
                content: body,
                title,
                metadata: serde_json::json!({
                    "url": url,
                    "status_code": status,
                    "content_type": ctype,
                }),
                fingerprint: None,
            });
        }
        Ok(out)
    }
}
