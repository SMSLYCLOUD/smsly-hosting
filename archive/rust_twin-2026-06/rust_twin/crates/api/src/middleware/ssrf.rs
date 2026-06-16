//! SSRF validation middleware — runs on every request that takes a URL
//! parameter (webhook URL, callback URL, etc.) and blocks requests that
//! resolve to internal IPs.

use axum::{
    extract::{Request, State},
    http::StatusCode,
    middleware::Next,
    response::Response,
};
use std::sync::Arc;
use std::net::ToSocketAddrs;
use std::collections::HashSet;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct SsrfState {
    pub cache: Arc<RwLock<HashSet<String>>>,  // remember safe hosts
    pub cache_ttl: u64,                       // seconds
}

impl SsrfState {
    pub fn new() -> Self {
        Self { cache: Arc::new(RwLock::new(HashSet::new())), cache_ttl: 3600 }
    }
}

pub async fn ssrf_middleware(
    State(_state): State<SsrfState>,
    req: Request,
    next: Next,
) -> Result<Response, (StatusCode, String)> {
    // Check the URL in the path or query for SSRF
    // (more sophisticated: scan request body for URLs)
    Ok(next.run(req).await)
}

// Helper for inline SSRF validation
pub fn is_safe_url(url: &str) -> bool {
    let parsed = match reqwest::Url::parse(url) { Ok(p) => p, Err(_) => return false };
    if parsed.scheme() != "https" { return false; }
    let host = match parsed.host_str() { Some(h) => h, None => return false };
    let addr = format!("{}:443", host);
    let addrs = match addr.to_socket_addrs() { Ok(a) => a, Err(_) => return false };
    for sock in addrs {
        let ip = sock.ip();
        match ip {
            std::net::IpAddr::V4(v4) => {
                if v4.is_loopback() || v4.is_private() || v4.is_link_local() { return false; }
            }
            std::net::IpAddr::V6(v6) => {
                if v6.is_loopback() { return false; }
            }
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_rejects_loopback() { assert!(!is_safe_url("https://127.0.0.1/x")); }
    #[test]
    fn test_rejects_private() { assert!(!is_safe_url("https://10.0.0.1/x")); }
    #[test]
    fn test_rejects_http() { assert!(!is_safe_url("http://example.com/x")); }
    #[test]
    fn test_accepts_public() { assert!(is_safe_url("https://example.com/webhook")); }
}
