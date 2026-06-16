//! Auth + security middleware for the Axum API.
//!
//! - `auth_user`: extracts the authenticated user from a JWT bearer token
//!   (used as `AuthUser` extractor in route handlers)
//! - `hmac`: HMAC V2 request authentication for service-to-service calls
//!   (lite agents, remote orchestrators)
//! - `ssrf`: validates URL parameters to block SSRF attacks
//! - `ratelimit`: per-IP token-bucket rate limiting

pub mod auth_user;
pub mod hmac;
pub mod ssrf;
pub mod ratelimit;

// Re-export AuthUser so existing `use crate::middleware::AuthUser` keeps working
pub use auth_user::AuthUser;
