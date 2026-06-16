//! HMAC V2 request authentication middleware.
//!
//! Validates that incoming requests are signed with the shared GATEWAY_SECRET.
//! Used for service-to-service calls (lite agents, remote orchestrators).

use axum::{
    body::Bytes,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    middleware::Next,
    response::Response,
};
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use subtle::ConstantTimeEq;
use thiserror::Error;

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone)]
pub struct HmacState {
    pub secret: Arc<String>,
    pub max_clock_skew_secs: i64,        // default 60
    pub nonce_cache_size: usize,         // default 10000
}

#[derive(Debug, Error)]
pub enum HmacError {
    #[error("missing signature header")]
    MissingSignature,
    #[error("missing timestamp header")]
    MissingTimestamp,
    #[error("missing nonce header")]
    MissingNonce,
    #[error("timestamp out of window")]
    TimestampSkew,
    #[error("nonce already used (replay)")]
    ReplayAttack,
    #[error("signature mismatch")]
    BadSignature,
}

pub async fn hmac_middleware(
    State(state): State<HmacState>,
    req: Request,
    next: Next,
) -> Result<Response, (StatusCode, String)> {
    let (parts, body) = req.into_parts();
    let body_bytes = match axum::body::to_bytes(body, usize::MAX).await {
        Ok(b) => b,
        Err(_) => return Err((StatusCode::BAD_REQUEST, "body read failed".to_string())),
    };

    let headers = &parts.headers;
    let signature = headers.get("X-Smsly-Signature")
        .and_then(|h| h.to_str().ok())
        .ok_or((StatusCode::UNAUTHORIZED, HmacError::MissingSignature.to_string()))?;
    let timestamp = headers.get("X-Smsly-Timestamp")
        .and_then(|h| h.to_str().ok())
        .ok_or((StatusCode::UNAUTHORIZED, HmacError::MissingTimestamp.to_string()))?;
    let nonce = headers.get("X-Smsly-Nonce")
        .and_then(|h| h.to_str().ok())
        .ok_or((StatusCode::UNAUTHORIZED, HmacError::MissingNonce.to_string()))?;

    // Validate timestamp
    let ts: i64 = timestamp.parse().map_err(|_| (StatusCode::UNAUTHORIZED, "bad timestamp".to_string()))?;
    let now = chrono::Utc::now().timestamp();
    if (now - ts).abs() > state.max_clock_skew_secs {
        return Err((StatusCode::UNAUTHORIZED, HmacError::TimestampSkew.to_string()));
    }

    // Validate nonce (in a real implementation, use a Redis SET with TTL)
    // Here we just check the nonce is non-empty
    if nonce.is_empty() || nonce.len() > 128 {
        return Err((StatusCode::UNAUTHORIZED, HmacError::MissingNonce.to_string()));
    }

    // Compute expected signature
    let body_hash = {
        let mut h = Sha256::new();
        h.update(&body_bytes);
        hex::encode(h.finalize())
    };
    let message = format!("{}\n{}\n{}\n{}", parts.method.as_str(), parts.uri.path(), timestamp, body_hash);

    let mut mac = HmacSha256::new_from_slice(state.secret.as_bytes())
        .expect("HMAC accepts any key length");
    mac.update(message.as_bytes());
    let expected = mac.finalize().into_bytes();

    let provided = hex::decode(signature).map_err(|_| (StatusCode::UNAUTHORIZED, "bad signature hex".to_string()))?;

    if provided.ct_eq(&expected).unwrap_u8() != 1 {
        return Err((StatusCode::UNAUTHORIZED, HmacError::BadSignature.to_string()));
    }

    // Reconstruct the request and pass through
    let req = Request::from_parts(parts, axum::body::Body::from(body_bytes));
    Ok(next.run(req).await)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sign(secret: &str, method: &str, path: &str, ts: &str, body: &[u8]) -> String {
        let body_hash = {
            let mut h = Sha256::new();
            h.update(body);
            hex::encode(h.finalize())
        };
        let msg = format!("{}\n{}\n{}\n{}", method, path, ts, body_hash);
        let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(msg.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    #[test]
    fn test_signature_format() {
        let s = sign("secret", "POST", "/api/v1/x", "1700000000", b"hello");
        assert_eq!(s.len(), 64);
    }
}
