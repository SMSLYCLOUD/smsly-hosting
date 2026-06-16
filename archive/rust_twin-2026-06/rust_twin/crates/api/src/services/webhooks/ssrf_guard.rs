//! SSRF guard for webhook delivery.
//!
//! Rejects URLs that resolve to:
//! - Loopback addresses (127.0.0.0/8, ::1, etc.)
//! - Private RFC 1918 ranges (10/8, 172.16/12, 192.168/16)
//! - Link-local (169.254/16, fe80::/10)
//! - Cloud metadata IPs (169.254.169.254, fd00:ec2::254)
//! - Hostnames matching internal service names (backend, db, redis, etc.)

use std::net::{IpAddr, ToSocketAddrs};
use thiserror::Error;

const BLOCKED_HOSTNAMES: &[&str] = &[
    "backend", "db", "redis", "rabbitmq", "registry", "caddy", "traefik",
    "frontend", "celery", "celery-beat", "celery-fast", "celery-deploy",
    "pgcat", "prometheus", "loki", "grafana", "cadvisor", "node-exporter",
    "socket-proxy", "frps", "route-fallback", "localhost",
    "metadata.google.internal", "metadata",
];

const METADATA_IPS: &[&str] = &[
    "169.254.169.254",  // AWS, GCP, Azure
    "fd00:ec2::254",    // AWS IPv6
    "169.254.170.2",    // ECS task metadata
];

#[derive(Debug, Error)]
pub enum SsrfError {
    #[error("URL resolves to blocked IP: {0}")]
    BlockedIp(IpAddr),
    #[error("URL uses blocked hostname: {0}")]
    BlockedHostname(String),
    #[error("URL uses metadata IP: {0}")]
    MetadataIp(String),
    #[error("URL is not https")]
    NotHttps,
    #[error("URL parse error: {0}")]
    Parse(String),
    #[error("DNS resolution failed: {0}")]
    Dns(String),
}

pub fn validate_url(url: &str) -> Result<(), SsrfError> {
    let parsed = url::Url::parse(url).map_err(|e| SsrfError::Parse(e.to_string()))?;

    if parsed.scheme() != "https" {
        return Err(SsrfError::NotHttps);
    }

    let host = parsed.host_str().ok_or_else(|| SsrfError::Parse("no host".to_string()))?;
    let host_lower = host.to_lowercase();

    // Check blocked hostnames
    if BLOCKED_HOSTNAMES.iter().any(|b| host_lower == *b || host_lower.ends_with(&format!(".{}", b))) {
        return Err(SsrfError::BlockedHostname(host.to_string()));
    }

    // Resolve to IP and check
    let port = parsed.port().unwrap_or(443);
    let addr = format!("{}:{}", host, port);
    let addrs: Vec<_> = addr.to_socket_addrs().map_err(|e| SsrfError::Dns(e.to_string()))?.collect();
    if addrs.is_empty() {
        return Err(SsrfError::Dns("no addresses".to_string()));
    }
    for sock_addr in &addrs {
        let ip = sock_addr.ip();
        if METADATA_IPS.contains(&ip.to_string().as_str()) {
            return Err(SsrfError::MetadataIp(ip.to_string()));
        }
        match ip {
            IpAddr::V4(v4) => {
                if v4.is_loopback() || v4.is_private() || v4.is_link_local() || v4.is_unspecified() {
                    return Err(SsrfError::BlockedIp(ip));
                }
            }
            IpAddr::V6(v6) => {
                if v6.is_loopback() || v6.is_unspecified() {
                    return Err(SsrfError::BlockedIp(ip));
                }
                // Check for ULA (fc00::/7) and link-local (fe80::/10)
                let seg = v6.segments();
                if (seg[0] & 0xfe00) == 0xfc00 || (seg[0] & 0xffc0) == 0xfe80 {
                    return Err(SsrfError::BlockedIp(ip));
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rejects_loopback() {
        assert!(validate_url("https://127.0.0.1/x").is_err());
        assert!(validate_url("https://localhost/x").is_err());
    }

    #[test]
    fn test_rejects_private() {
        assert!(validate_url("https://10.0.0.1/x").is_err());
        assert!(validate_url("https://192.168.1.1/x").is_err());
    }

    #[test]
    fn test_rejects_metadata() {
        assert!(validate_url("https://169.254.169.254/latest/meta-data/").is_err());
    }

    #[test]
    fn test_rejects_internal_hostnames() {
        assert!(validate_url("https://backend/api").is_err());
        assert!(validate_url("https://db:5432").is_err());
    }

    #[test]
    fn test_rejects_http() {
        assert!(validate_url("http://example.com/x").is_err());
    }

    #[test]
    fn test_accepts_public_https() {
        // example.com resolves to a public IP
        assert!(validate_url("https://example.com/webhook").is_ok());
    }
}
