//! ACME HTTP-01 challenge handler.
//!
//! Per RFC 8555, the ACME server fetches
//! `http://<domain>/.well-known/acme-challenge/<token>` to verify domain control.
//! The verification token is stored when the domain is verified; this handler
//! serves the challenge response when requested.

use axum::{
    extract::{Host, Path},
    http::StatusCode,
    response::IntoResponse,
};
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};

// In-memory token store (replace with DB in production).
// Key: "<domain>|<token>", Value: key_authorization (token + "." + thumbprint)
type TokenStore = Arc<Mutex<HashMap<String, String>>>;

static TOKEN_STORE: LazyLock<TokenStore> =
    LazyLock::new(|| Arc::new(Mutex::new(HashMap::new())));

/// Store a token + key_authorization for a domain.
/// Called when the verification request is initiated.
pub fn store_token(domain: &str, token: &str, key_authorization: &str) {
    let key = format!("{}|{}", domain, token);
    TOKEN_STORE.lock().unwrap().insert(key, key_authorization.to_string());
}

pub async fn acme_challenge(
    Host(host): Host,
    Path(token): Path<String>,
) -> impl IntoResponse {
    // host is the Host header (e.g., "example.com:80" — strip port)
    let domain = host.split(':').next().unwrap_or(&host).to_string();
    let key = format!("{}|{}", domain, token);
    if let Some(key_authz) = TOKEN_STORE.lock().unwrap().get(&key) {
        (
            StatusCode::OK,
            [("content-type", "text/plain")],
            key_authz.clone(),
        )
    } else {
        (
            StatusCode::NOT_FOUND,
            [("content-type", "text/plain")],
            format!("token not found for {}/{}", domain, token),
        )
    }
}

/// ACME directory endpoint (informational).
pub async fn acme_directory() -> impl IntoResponse {
    axum::Json(serde_json::json!({
        "new-authz": "/acme/new-authz",
        "new-cert": "/acme/new-cert",
        "new-reg": "/acme/new-reg",
        "revoke-cert": "/acme/revoke-cert",
        "key-change": "/acme/key-change",
        "new-nonce": "/acme/new-nonce",
        "meta": {
            "terms-of-service": "https://example.com/tos",
            "website": "https://example.com",
            "caa-identities": ["smsly.cloud"],
        },
    }))
}
