use axum::{
    async_trait,
    extract::FromRequestParts,
    http::{request::Parts, StatusCode},
};
use std::sync::Arc;
use tracing::warn;

use crate::AppState;
use cn_core::auth::AuthUtils;

/// Represents an authenticated user extracted from the JWT token.
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
        // 1. Get the Authorization header
        let auth_header = parts
            .headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok());

        // 2. Validate format "Bearer <token>"
        let token = match auth_header {
            Some(value) if value.starts_with("Bearer ") => &value[7..],
            _ => {
                warn!("Missing or invalid Authorization header");
                return Err((
                    StatusCode::UNAUTHORIZED,
                    "Missing Bearer token".to_string(),
                ));
            }
        };

        // 3. Decode JWT and validate signature against the global SECRET_KEY
        let claims = AuthUtils::decode_jwt(token, &state.config.secret_key).map_err(|e| {
            warn!("JWT validation failed: {}", e);
            (StatusCode::UNAUTHORIZED, "Invalid or expired token".to_string())
        })?;

        // 4. Optionally: verify user still exists in DB (skipped for performance, rely on JWT expiration)

        Ok(AuthUser { id: claims.sub })
    }
}