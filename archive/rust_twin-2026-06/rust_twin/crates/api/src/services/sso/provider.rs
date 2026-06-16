//! OAuth provider abstraction. Each provider (GitHub, GitLab, Bitbucket)
//! implements this trait.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct TokenSet {
    pub access_token: String,
    pub refresh_token: Option<String>,
    pub expires_at: Option<chrono::DateTime<chrono::Utc>>,
    pub scope: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct UserInfo {
    pub provider_uid: String,
    pub username: String,
    pub email: Option<String>,
    pub display_name: Option<String>,
    pub avatar_url: Option<String>,
    pub profile_url: Option<String>,
}

#[derive(Debug, Error)]
pub enum OAuthError {
    #[error("HTTP error: {0}")]
    Http(String),
    #[error("provider returned error: {0}")]
    Provider(String),
    #[error("invalid response: {0}")]
    Parse(String),
    #[error("state mismatch (CSRF protection)")]
    StateMismatch,
}

#[async_trait]
pub trait OAuthProvider: Send + Sync {
    fn name(&self) -> &'static str;
    fn authorize_url(&self, state: &str, redirect_uri: &str, scopes: &[&str]) -> String;
    async fn exchange_code(&self, code: &str, redirect_uri: &str) -> Result<TokenSet, OAuthError>;
    async fn fetch_user_info(&self, token: &TokenSet) -> Result<UserInfo, OAuthError>;
}

/// application/x-www-form-urlencoded encoder.
///
/// Reserved per RFC 3986 unreserved set: `A-Z a-z 0-9 - _ . ~`.
/// Spaces become `+` (form encoding); everything else is `%XX`.
pub(crate) fn form_urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
                (b as char).to_string()
            } else if b == b' ' {
                "+".to_string()
            } else {
                format!("%{:02X}", b)
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_form_urlencode_space_becomes_plus() {
        assert_eq!(form_urlencode("hello world"), "hello+world");
    }

    #[test]
    fn test_form_urlencode_colon_percent_encoded() {
        assert_eq!(form_urlencode("user:email"), "user%3Aemail");
    }

    #[test]
    fn test_form_urlencode_unreserved_unchanged() {
        assert_eq!(form_urlencode("abc-_.~XYZ"), "abc-_.~XYZ");
    }
}
