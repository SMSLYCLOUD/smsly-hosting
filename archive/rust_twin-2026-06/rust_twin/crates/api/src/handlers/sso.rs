//! SSO/OAuth HTTP handlers.

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Redirect},
    Json,
};
use sea_orm::{ColumnTrait, EntityTrait, QueryFilter};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use crate::{AppState, middleware::AuthUser};
use crate::services::sso::provider::OAuthProvider;
use crate::services::sso::github::GitHubProvider;
use crate::services::sso::gitlab::GitLabProvider;
use crate::services::sso::bitbucket::BitbucketProvider;
use cn_core::entities::social_account;

pub fn provider_for(name: &str, client_id: &str, client_secret: &str) -> Option<Box<dyn OAuthProvider>> {
    match name {
        "github" => Some(Box::new(GitHubProvider::new(client_id.into(), client_secret.into()))),
        "gitlab" | "gitlab_oauth2" => Some(Box::new(GitLabProvider::new(client_id.into(), client_secret.into()))),
        "bitbucket" | "bitbucket_oauth2" => Some(Box::new(BitbucketProvider::new(client_id.into(), client_secret.into()))),
        _ => None,
    }
}

#[derive(Debug, Deserialize)]
pub struct AuthorizeQuery {
    pub provider: String,
    pub redirect_uri: String,
}

pub async fn oauth_authorize(
    State(_state): State<Arc<AppState>>,
    Query(q): Query<AuthorizeQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let provider = provider_for(&q.provider, "placeholder_client_id", "placeholder_secret")
        .ok_or_else(|| (StatusCode::NOT_FOUND, format!("unknown provider: {}", q.provider)))?;
    let state_token = generate_state_token();
    let url = provider.authorize_url(&state_token, &q.redirect_uri, &["user:email", "read:user"]);
    Ok(Redirect::to(&url))
}

#[derive(Debug, Deserialize)]
pub struct CallbackQuery {
    pub code: String,
    pub state: String,
    pub provider: String,
}

pub async fn oauth_callback(
    State(_state): State<Arc<AppState>>,
    Query(_q): Query<CallbackQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    // In a real implementation:
    // 1. Verify state matches the session
    // 2. Exchange code for token via provider.exchange_code
    // 3. Fetch user info via provider.fetch_user_info
    // 4. Find or create user
    // 5. Create social_account record
    // 6. Redirect to dashboard
    Ok(Json(serde_json::json!({
        "status": "ok",
        "note": "OAuth callback handler is a stub; see docs/RUST_TWIN_POLARITY.md",
    })))
}

#[derive(Debug, Serialize)]
pub struct SocialAccountResponse {
    pub id: i32,
    pub user_id: i32,
    pub provider: String,
    pub uid: String,
    pub extra_data: String,
    pub date_joined: chrono::DateTime<chrono::Utc>,
    pub last_login: Option<chrono::DateTime<chrono::Utc>>,
}

impl From<social_account::Model> for SocialAccountResponse {
    fn from(a: social_account::Model) -> Self {
        Self {
            id: a.id,
            user_id: a.user_id,
            provider: a.provider,
            uid: a.uid,
            extra_data: a.extra_data,
            date_joined: a.date_joined.with_timezone(&chrono::Utc),
            last_login: a.last_login.map(|dt| dt.with_timezone(&chrono::Utc)),
        }
    }
}

pub async fn list_social_accounts(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let accounts = social_account::Entity::find()
        .filter(social_account::Column::UserId.eq(auth.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<SocialAccountResponse> = accounts.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

fn generate_state_token() -> String {
    Uuid::new_v4().simple().to_string()
}
