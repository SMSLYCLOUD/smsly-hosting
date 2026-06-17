//! AuthUser extractor — represents an authenticated user.
//!
//! Three sources are accepted (in order):
//!
//! 1. `Authorization: Bearer <jwt>` — HS256 JWT signed with
//!    `state.config.secret_key` (existing behaviour, kept for backward
//!    compatibility with the SDKs and CLI tools that use the
//!    `access_token` from `/auth/login`).
//! 2. `Cookie: __Host-smsly_token=<drf-token>` — set by the login handler
//!    (and by the Django backend). The `__Host-` prefix is part of the
//!    cookie name; we strip it only when normalising look-ups so the same
//!    value can also be used in an `Authorization: Token` header.
//! 3. `Authorization: Token <40-char-hex>` — Django REST Framework style.
//!    The token is looked up in the process-local `DrfTokenStore` on
//!    `AppState`.
//!
//! All three paths resolve to `AuthUser { id }`.

use axum::{
    async_trait,
    extract::FromRequestParts,
    http::{header::AUTHORIZATION, request::Parts, HeaderMap, StatusCode},
};
use std::sync::Arc;
use tracing::warn;

use crate::AppState;
use cn_core::auth::AuthUtils;

const DRF_COOKIE_NAME: &str = "__Host-smsly_token";

pub struct AuthUser {
    pub id: i32,
}

#[async_trait]
impl FromRequestParts<Arc<AppState>> for AuthUser {
    type Rejection = (StatusCode, String);

    async fn from_request_parts(
        parts: &mut Parts,
        state: &Arc<AppState>,
    ) -> Result<Self, Self::Rejection> {
        // Path 1: Authorization: Bearer <jwt>
        if let Some(jwt) = bearer_token(&parts.headers) {
            match AuthUtils::decode_jwt(jwt, &state.config.secret_key) {
                Ok(claims) => return Ok(AuthUser { id: claims.sub }),
                Err(e) => {
                    warn!("JWT validation failed: {}", e);
                    // fall through and try other sources so a stale cookie
                    // doesn't lock the user out
                }
            }
        }

        // Path 2: __Host-smsly_token cookie (DRF token)
        if let Some(cookie_token) = cookie_value(&parts.headers, DRF_COOKIE_NAME) {
            if let Some(uid) = state.drf_tokens.resolve(&cookie_token) {
                return Ok(AuthUser { id: uid });
            }
        }

        // Path 3: Authorization: Token <40-char-hex> (DRF style)
        if let Some(drf_token) = drf_bearer_token(&parts.headers) {
            if let Some(uid) = state.drf_tokens.resolve(drf_token) {
                return Ok(AuthUser { id: uid });
            }
        }

        Err((
            StatusCode::UNAUTHORIZED,
            "Missing or invalid authentication credentials".to_string(),
        ))
    }
}

/// Extract a `Bearer <token>` value from the `Authorization` header.
fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .map(|s| s.trim())
}

/// Extract a `Token <token>` value (DRF style) from the `Authorization` header.
fn drf_bearer_token(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Token "))
        .map(|s| s.trim())
}

/// Extract a single named cookie from the `Cookie` header. The `__Host-`
/// prefix is enforced by browsers in the `Set-Cookie` response and is part
/// of the cookie name in the request, so we look up the prefixed name
/// directly.
fn cookie_value<'a>(headers: &'a HeaderMap, name: &str) -> Option<String> {
    let raw = headers
        .get(axum::http::header::COOKIE)
        .and_then(|v| v.to_str().ok())?;
    for part in raw.split(';') {
        let part = part.trim();
        if let Some((k, v)) = part.split_once('=') {
            if k == name {
                return Some(v.to_string());
            }
        }
    }
    None
}
