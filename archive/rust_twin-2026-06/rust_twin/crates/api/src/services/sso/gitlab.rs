use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;

use super::provider::{form_urlencode, OAuthError, OAuthProvider, TokenSet, UserInfo};

const GITLAB_BASE_URL: &str = "https://gitlab.com";

pub struct GitLabProvider {
    pub client_id: String,
    pub client_secret: String,
    /// Base URL for self-hosted GitLab. Defaults to https://gitlab.com.
    pub base_url: String,
}

impl GitLabProvider {
    pub fn new(client_id: String, client_secret: String) -> Self {
        Self {
            client_id,
            client_secret,
            base_url: GITLAB_BASE_URL.to_string(),
        }
    }

    pub fn with_base_url(client_id: String, client_secret: String, base_url: String) -> Self {
        Self { client_id, client_secret, base_url }
    }
}

#[async_trait]
impl OAuthProvider for GitLabProvider {
    fn name(&self) -> &'static str {
        "gitlab"
    }

    fn authorize_url(&self, state: &str, redirect_uri: &str, scopes: &[&str]) -> String {
        let scope = scopes.join(" ");
        format!(
            "{}/oauth/authorize?client_id={}&redirect_uri={}&response_type=code&state={}&scope={}",
            self.base_url,
            form_urlencode(&self.client_id),
            form_urlencode(redirect_uri),
            form_urlencode(state),
            form_urlencode(&scope),
        )
    }

    async fn exchange_code(&self, code: &str, redirect_uri: &str) -> Result<TokenSet, OAuthError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        let resp = client
            .post(format!("{}/oauth/token", self.base_url))
            .header("Accept", "application/json")
            .form(&[
                ("client_id", self.client_id.as_str()),
                ("client_secret", self.client_secret.as_str()),
                ("code", code),
                ("grant_type", "authorization_code"),
                ("redirect_uri", redirect_uri),
            ])
            .send()
            .await
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(OAuthError::Provider(format!("status {}", resp.status())));
        }
        #[derive(Deserialize)]
        struct Resp {
            access_token: String,
            #[serde(default)]
            scope: String,
            refresh_token: Option<String>,
            expires_in: Option<i64>,
        }
        let r: Resp = resp
            .json()
            .await
            .map_err(|e| OAuthError::Parse(e.to_string()))?;
        Ok(TokenSet {
            access_token: r.access_token,
            refresh_token: r.refresh_token,
            expires_at: r
                .expires_in
                .map(|s| chrono::Utc::now() + chrono::Duration::seconds(s)),
            scope: r.scope,
        })
    }

    async fn fetch_user_info(&self, token: &TokenSet) -> Result<UserInfo, OAuthError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        let resp = client
            .get(format!("{}/api/v4/user", self.base_url))
            .header("Authorization", format!("Bearer {}", token.access_token))
            .header("Accept", "application/json")
            .send()
            .await
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(OAuthError::Provider(format!("status {}", resp.status())));
        }
        #[derive(Deserialize)]
        struct GlUser {
            id: i64,
            username: String,
            name: Option<String>,
            email: Option<String>,
            avatar_url: Option<String>,
            web_url: Option<String>,
        }
        let u: GlUser = resp
            .json()
            .await
            .map_err(|e| OAuthError::Parse(e.to_string()))?;
        Ok(UserInfo {
            provider_uid: u.id.to_string(),
            username: u.username,
            email: u.email,
            display_name: u.name,
            avatar_url: u.avatar_url,
            profile_url: u.web_url,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_name_is_gitlab() {
        let p = GitLabProvider::new("cid".into(), "cs".into());
        assert_eq!(p.name(), "gitlab");
    }

    #[test]
    fn test_authorize_url_defaults_to_gitlab_com() {
        let p = GitLabProvider::new("cid".into(), "cs".into());
        let url = p.authorize_url("st", "https://example.com/cb", &["read_user", "read_api"]);
        assert!(url.starts_with("https://gitlab.com/oauth/authorize?"));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("scope=read_user+read_api"));
    }

    #[test]
    fn test_authorize_url_honours_custom_base() {
        let p = GitLabProvider::with_base_url(
            "cid".into(),
            "cs".into(),
            "https://git.internal.example".into(),
        );
        let url = p.authorize_url("st", "https://example.com/cb", &["read_user"]);
        assert!(url.starts_with("https://git.internal.example/oauth/authorize?"));
    }
}
