use async_trait::async_trait;
use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;

use super::provider::{form_urlencode, OAuthError, OAuthProvider, TokenSet, UserInfo};

pub struct BitbucketProvider {
    pub client_id: String,
    pub client_secret: String,
}

impl BitbucketProvider {
    pub fn new(client_id: String, client_secret: String) -> Self {
        Self { client_id, client_secret }
    }
}

#[async_trait]
impl OAuthProvider for BitbucketProvider {
    fn name(&self) -> &'static str {
        "bitbucket"
    }

    fn authorize_url(&self, state: &str, redirect_uri: &str, scopes: &[&str]) -> String {
        // Bitbucket does not require `scope` in the authorize URL when scopes
        // are configured server-side on the consumer, but we honour an
        // explicit set when supplied.
        let mut url = format!(
            "https://bitbucket.org/site/oauth2/authorize?client_id={}&redirect_uri={}&response_type=code&state={}",
            form_urlencode(&self.client_id),
            form_urlencode(redirect_uri),
            form_urlencode(state),
        );
        if !scopes.is_empty() {
            let scope = scopes.join(" ");
            url.push_str(&format!("&scope={}", form_urlencode(&scope)));
        }
        url
    }

    async fn exchange_code(&self, code: &str, redirect_uri: &str) -> Result<TokenSet, OAuthError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        let resp = client
            .post("https://bitbucket.org/site/oauth2/access_token")
            .basic_auth(&self.client_id, Some(&self.client_secret))
            .header("Accept", "application/json")
            .form(&[
                ("grant_type", "authorization_code"),
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
            scopes: String,
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
            scope: r.scopes,
        })
    }

    async fn fetch_user_info(&self, token: &TokenSet) -> Result<UserInfo, OAuthError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        let resp = client
            .get("https://api.bitbucket.org/2.0/user")
            .header("Authorization", format!("Bearer {}", token.access_token))
            .header("Accept", "application/json")
            .send()
            .await
            .map_err(|e| OAuthError::Http(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(OAuthError::Provider(format!("status {}", resp.status())));
        }
        #[derive(Deserialize)]
        struct BbLinkHref {
            href: Option<String>,
        }
        #[derive(Deserialize, Default)]
        struct BbLinks {
            avatar: Option<BbLinkHref>,
            html: Option<BbLinkHref>,
        }
        #[derive(Deserialize)]
        struct BbUser {
            account_id: Option<String>,
            uuid: Option<String>,
            username: Option<String>,
            display_name: Option<String>,
            #[serde(default)]
            links: BbLinks,
        }
        let u: BbUser = resp
            .json()
            .await
            .map_err(|e| OAuthError::Parse(e.to_string()))?;
        let provider_uid = u
            .account_id
            .clone()
            .or_else(|| u.uuid.clone())
            .ok_or_else(|| OAuthError::Parse("missing account_id/uuid".into()))?;
        let username = u
            .username
            .clone()
            .or_else(|| u.display_name.clone())
            .unwrap_or_else(|| provider_uid.clone());
        Ok(UserInfo {
            provider_uid,
            username,
            email: None,
            display_name: u.display_name,
            avatar_url: u.links.avatar.and_then(|l| l.href),
            profile_url: u.links.html.and_then(|l| l.href),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_name_is_bitbucket() {
        let p = BitbucketProvider::new("cid".into(), "cs".into());
        assert_eq!(p.name(), "bitbucket");
    }

    #[test]
    fn test_authorize_url_basic() {
        let p = BitbucketProvider::new("cid".into(), "cs".into());
        let url = p.authorize_url("st", "https://example.com/cb", &[]);
        assert!(url.starts_with("https://bitbucket.org/site/oauth2/authorize?"));
        assert!(url.contains("response_type=code"));
        assert!(url.contains("state=st"));
        assert!(!url.contains("scope="));
    }

    #[test]
    fn test_authorize_url_with_scopes() {
        let p = BitbucketProvider::new("cid".into(), "cs".into());
        let url = p.authorize_url("st", "https://example.com/cb", &["account", "repository"]);
        assert!(url.contains("scope=account+repository"));
    }
}
