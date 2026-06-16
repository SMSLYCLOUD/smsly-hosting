use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;

use super::provider::{form_urlencode, OAuthError, OAuthProvider, TokenSet, UserInfo};

pub struct GitHubProvider {
    pub client_id: String,
    pub client_secret: String,
}

impl GitHubProvider {
    pub fn new(client_id: String, client_secret: String) -> Self {
        Self { client_id, client_secret }
    }
}

#[async_trait]
impl OAuthProvider for GitHubProvider {
    fn name(&self) -> &'static str {
        "github"
    }

    fn authorize_url(&self, state: &str, redirect_uri: &str, scopes: &[&str]) -> String {
        let scope = scopes.join(" ");
        format!(
            "https://github.com/login/oauth/authorize?client_id={}&redirect_uri={}&state={}&scope={}",
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
            .post("https://github.com/login/oauth/access_token")
            .header("Accept", "application/json")
            .form(&[
                ("client_id", self.client_id.as_str()),
                ("client_secret", self.client_secret.as_str()),
                ("code", code),
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
            .get("https://api.github.com/user")
            .header("Authorization", format!("Bearer {}", token.access_token))
            .header("User-Agent", "smsly")
            .header("Accept", "application/json")
            .send()
            .await
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(OAuthError::Provider(format!("status {}", resp.status())));
        }
        #[derive(Deserialize)]
        struct GhUser {
            id: i64,
            login: String,
            name: Option<String>,
            email: Option<String>,
            avatar_url: Option<String>,
            html_url: Option<String>,
        }
        let u: GhUser = resp
            .json()
            .await
            .map_err(|e| OAuthError::Parse(e.to_string()))?;
        Ok(UserInfo {
            provider_uid: u.id.to_string(),
            username: u.login,
            email: u.email,
            display_name: u.name,
            avatar_url: u.avatar_url,
            profile_url: u.html_url,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_name_is_github() {
        let p = GitHubProvider::new("cid".into(), "cs".into());
        assert_eq!(p.name(), "github");
    }

    #[test]
    fn test_authorize_url_contains_state() {
        let p = GitHubProvider::new("cid".into(), "cs".into());
        let url = p.authorize_url("xyz123", "https://example.com/cb", &["user:email", "repo"]);
        assert!(url.contains("state=xyz123"));
        assert!(url.contains("scope=user%3Aemail+repo"));
    }

    #[test]
    fn test_authorize_url_encodes_redirect() {
        let p = GitHubProvider::new("cid".into(), "cs".into());
        let url = p.authorize_url("st", "https://example.com/cb?x=1", &["repo"]);
        assert!(url.contains("redirect_uri=https%3A%2F%2Fexample.com%2Fcb%3Fx%3D1"));
        assert!(url.contains("client_id=cid"));
    }
}
