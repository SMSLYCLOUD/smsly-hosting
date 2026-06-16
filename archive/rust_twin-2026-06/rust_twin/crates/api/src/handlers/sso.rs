//! SSO/OAuth HTTP handlers — real flow with state, code exchange, user linking.

use axum::{
    extract::{Path, Query, State},
    http::{header, HeaderValue, StatusCode},
    response::{IntoResponse, Redirect},
    Json,
};
use chrono::Utc;
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, ModelTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};
use uuid::Uuid;

use crate::middleware::AuthUser;
use crate::AppState;
use crate::services::sso::bitbucket::BitbucketProvider;
use crate::services::sso::github::GitHubProvider;
use crate::services::sso::gitlab::GitLabProvider;
use crate::services::sso::provider::{OAuthProvider, TokenSet, UserInfo};
use cn_core::auth::AuthUtils;
use cn_core::entities::{social_account, social_app, social_token, user};

const AUTH_COOKIE_NAME: &str = "auth_token";
const COOKIE_MAX_AGE_SECS: i64 = 60 * 60 * 24 * 30; // 30 days
const STATE_TTL_SECS: i64 = 600; // 10 minutes

#[derive(Debug, Clone)]
#[allow(dead_code)] // fields retained for upcoming `link to existing user` flow
struct StateEntry {
    pub user_id: i32, // 0 if signup / not yet authenticated
    pub provider: String,
    pub redirect_after: String,
    pub created_at: chrono::DateTime<Utc>,
}

// In-memory state store. In production this should be backed by Redis
// (`state.redis`) so flows survive across processes and replicas.
static STATE_STORE: LazyLock<Mutex<HashMap<String, StateEntry>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

pub fn provider_for(
    name: &str,
    client_id: &str,
    client_secret: &str,
) -> Option<Box<dyn OAuthProvider>> {
    match name {
        "github" => Some(Box::new(GitHubProvider::new(
            client_id.into(),
            client_secret.into(),
        ))),
        "gitlab" | "gitlab_oauth2" => Some(Box::new(GitLabProvider::new(
            client_id.into(),
            client_secret.into(),
        ))),
        "bitbucket" | "bitbucket_oauth2" => Some(Box::new(BitbucketProvider::new(
            client_id.into(),
            client_secret.into(),
        ))),
        _ => None,
    }
}

#[derive(Debug, Deserialize)]
pub struct AuthorizeQuery {
    pub provider: String,
    pub redirect_uri: String,
    pub user_id: Option<i32>,
}

#[derive(Debug, Deserialize)]
pub struct CallbackQuery {
    pub code: String,
    pub state: String,
    pub provider: String,
}

pub async fn oauth_authorize(
    State(state): State<Arc<AppState>>,
    Query(q): Query<AuthorizeQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let (client_id, client_secret) =
        lookup_oauth_credentials(&state, &q.provider).await.ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                format!("OAuth provider '{}' not configured", q.provider),
            )
        })?;
    let provider = provider_for(&q.provider, &client_id, &client_secret)
        .ok_or_else(|| (StatusCode::NOT_FOUND, format!("unknown provider: {}", q.provider)))?;

    let state_token = generate_state_token();
    let entry = StateEntry {
        user_id: q.user_id.unwrap_or(0),
        provider: q.provider.clone(),
        redirect_after: q.redirect_uri.clone(),
        created_at: Utc::now(),
    };
    STATE_STORE
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("state lock: {}", e)))?
        .insert(state_token.clone(), entry);

    let url = provider.authorize_url(
        &state_token,
        &q.redirect_uri,
        &["user:email", "read:user"],
    );
    Ok(Redirect::to(&url))
}

pub async fn oauth_callback(
    State(state): State<Arc<AppState>>,
    Query(q): Query<CallbackQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    // 1. Verify state (single-use, bound to the initiating flow).
    let state_entry = {
        let mut map = STATE_STORE
            .lock()
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("state lock: {}", e)))?;
        map.remove(&q.state)
            .ok_or_else(|| {
                (
                    StatusCode::BAD_REQUEST,
                    "invalid or expired state".to_string(),
                )
            })?
    };
    if (Utc::now() - state_entry.created_at).num_seconds() > STATE_TTL_SECS {
        return Err((StatusCode::BAD_REQUEST, "state expired".to_string()));
    }

    // 2. Look up credentials and provider.
    let (client_id, client_secret) = lookup_oauth_credentials(&state, &q.provider)
        .await
        .ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                format!("OAuth provider '{}' not configured", q.provider),
            )
        })?;
    let provider = provider_for(&q.provider, &client_id, &client_secret)
        .ok_or_else(|| (StatusCode::NOT_FOUND, "unknown provider".to_string()))?;

    // 3. Exchange code for token, then fetch user info.
    let token = provider
        .exchange_code(&q.code, &state_entry.redirect_after)
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("code exchange: {}", e)))?;
    let user_info = provider
        .fetch_user_info(&token)
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("user info: {}", e)))?;

    // 4. Find or create the local user.
    let user_id = find_or_create_user(&state, &q.provider, &user_info)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // 5. Create / update the social_account + social_token records.
    let account_id = upsert_social_account(&state, user_id, &q.provider, &user_info)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;
    upsert_social_token(&state, account_id, &token)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // 6. Issue a session JWT and set it as the auth cookie, then redirect.
    let access_token = AuthUtils::create_jwt(user_id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let secure = !state.config.debug;
    let cookie = build_auth_cookie(&access_token, secure);
    let cookie_value = HeaderValue::from_str(&cookie)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok((
        StatusCode::SEE_OTHER,
        [
            (header::SET_COOKIE, cookie_value),
            (header::LOCATION, HeaderValue::from_static("/dashboard")),
        ],
    ))
}

#[derive(Debug, Serialize)]
pub struct SocialAccountResponse {
    pub id: i32,
    pub user_id: i32,
    pub provider: String,
    pub uid: String,
    pub extra_data: String,
    pub date_joined: chrono::DateTime<Utc>,
    pub last_login: Option<chrono::DateTime<Utc>>,
}

impl From<social_account::Model> for SocialAccountResponse {
    fn from(a: social_account::Model) -> Self {
        Self {
            id: a.id,
            user_id: a.user_id,
            provider: a.provider,
            uid: a.uid,
            extra_data: a.extra_data,
            date_joined: a.date_joined.with_timezone(&Utc),
            last_login: a.last_login.map(|dt| dt.with_timezone(&Utc)),
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

pub async fn disconnect_social(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(provider_uid): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let account = social_account::Entity::find()
        .filter(social_account::Column::UserId.eq(auth.id))
        .filter(social_account::Column::Provider.eq(&provider_uid))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "social account not found".to_string()))?;
    account
        .delete(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

// === Helpers ===

fn generate_state_token() -> String {
    Uuid::new_v4().simple().to_string()
}

fn build_auth_cookie(token: &str, secure: bool) -> String {
    let mut parts = vec![
        format!("{}={}", AUTH_COOKIE_NAME, token),
        "HttpOnly".to_string(),
        "SameSite=Strict".to_string(),
        format!("Max-Age={}", COOKIE_MAX_AGE_SECS),
        "Path=/".to_string(),
    ];
    if secure {
        parts.push("Secure".to_string());
    }
    parts.join("; ")
}

/// Resolve OAuth client credentials.
///
/// First try the `socialaccount_socialapp` table (any active row matching the
/// requested provider). Fall back to `OAUTH_<PROVIDER>_CLIENT_ID` /
/// `OAUTH_<PROVIDER>_CLIENT_SECRET` env vars for self-hosted deployments
/// that do not provision rows in the `social_app` table.
async fn lookup_oauth_credentials(
    state: &AppState,
    provider: &str,
) -> Option<(String, String)> {
    if let Ok(Some(app)) = social_app::Entity::find()
        .filter(social_app::Column::Provider.eq(provider))
        .filter(social_app::Column::IsActive.eq(true))
        .one(&state.db)
        .await
    {
        return Some((app.client_id, app.secret));
    }
    let env_var_client = format!(
        "OAUTH_{}_CLIENT_ID",
        provider.to_uppercase().replace('-', "_")
    );
    let env_var_secret = format!(
        "OAUTH_{}_CLIENT_SECRET",
        provider.to_uppercase().replace('-', "_")
    );
    let client_id = std::env::var(&env_var_client).ok()?;
    let client_secret = std::env::var(&env_var_secret).ok()?;
    Some((client_id, client_secret))
}

async fn find_or_create_user(
    state: &AppState,
    provider: &str,
    info: &UserInfo,
) -> Result<i32, String> {
    // 1. Look for an existing social_account linking this provider+uid to a user.
    if let Some(acct) = social_account::Entity::find()
        .filter(social_account::Column::Provider.eq(provider))
        .filter(social_account::Column::Uid.eq(&info.provider_uid))
        .one(&state.db)
        .await
        .map_err(|e| e.to_string())?
    {
        return Ok(acct.user_id);
    }
    // 2. Fall back to matching by verified email.
    if let Some(email) = &info.email {
        if let Some(u) = user::Entity::find()
            .filter(user::Column::Email.eq(email))
            .one(&state.db)
            .await
            .map_err(|e| e.to_string())?
        {
            return Ok(u.id);
        }
    }
    // 3. Otherwise create a brand-new user. OAuth-only accounts have an
    //    empty `password` so they cannot sign in via the password form.
    let now = Utc::now();
    let username = if info.username.is_empty() {
        let prefix_len = 8.min(info.provider_uid.len());
        format!("oauth_{}", &info.provider_uid[..prefix_len])
    } else {
        info.username.clone()
    };
    let fallback_email = format!("{}@oauth.local", username);
    let new_user = user::ActiveModel {
        username: Set(username),
        email: Set(info.email.clone().unwrap_or(fallback_email)),
        password: Set(String::new()),
        is_superuser: Set(false),
        is_staff: Set(false),
        is_active: Set(true),
        date_joined: Set(now.into()),
        ..Default::default()
    };
    let inserted = new_user
        .insert(&state.db)
        .await
        .map_err(|e| e.to_string())?;
    Ok(inserted.id)
}

async fn upsert_social_account(
    state: &AppState,
    user_id: i32,
    provider: &str,
    info: &UserInfo,
) -> Result<i32, String> {
    let now = Utc::now();
    let extra_data = serde_json::to_string(info).unwrap_or_default();

    if let Some(acct) = social_account::Entity::find()
        .filter(social_account::Column::UserId.eq(user_id))
        .filter(social_account::Column::Provider.eq(provider))
        .one(&state.db)
        .await
        .map_err(|e| e.to_string())?
    {
        let mut active: social_account::ActiveModel = acct.into();
        active.uid = Set(info.provider_uid.clone());
        active.extra_data = Set(extra_data);
        active.last_login = Set(Some(now.into()));
        let updated = active
            .update(&state.db)
            .await
            .map_err(|e| e.to_string())?;
        return Ok(updated.id);
    }

    let new_acct = social_account::ActiveModel {
        user_id: Set(user_id),
        provider: Set(provider.to_string()),
        uid: Set(info.provider_uid.clone()),
        extra_data: Set(extra_data),
        date_joined: Set(now.into()),
        last_login: Set(Some(now.into())),
        ..Default::default()
    };
    let inserted = new_acct
        .insert(&state.db)
        .await
        .map_err(|e| e.to_string())?;
    Ok(inserted.id)
}

async fn upsert_social_token(
    state: &AppState,
    account_id: i32,
    token: &TokenSet,
) -> Result<(), String> {
    let now = Utc::now();
    // If a token already exists for this account, refresh its fields;
    // otherwise insert a new row. The `social_token.account_id` link is
    // unique-by-convention in this codebase.
    if let Some(existing) = social_token::Entity::find()
        .filter(social_token::Column::AccountId.eq(account_id))
        .one(&state.db)
        .await
        .map_err(|e| e.to_string())?
    {
        let mut active: social_token::ActiveModel = existing.into();
        active.token = Set(token.access_token.clone());
        active.token_secret = Set(token.refresh_token.clone());
        active.expires_at = Set(token.expires_at.map(|dt| dt.with_timezone(&Utc).into()));
        active.updated_at = Set(now.into());
        active
            .update(&state.db)
            .await
            .map_err(|e| e.to_string())?;
    } else {
        let new_token = social_token::ActiveModel {
            account_id: Set(account_id),
            app_id: Set(0), // not linked to a social_app row when resolved from env
            token: Set(token.access_token.clone()),
            token_secret: Set(token.refresh_token.clone()),
            expires_at: Set(token.expires_at.map(|dt| dt.with_timezone(&Utc).into())),
            created_at: Set(now.into()),
            updated_at: Set(now.into()),
            ..Default::default()
        };
        new_token
            .insert(&state.db)
            .await
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}
