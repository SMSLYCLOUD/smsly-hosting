//! Auth HTTP handlers — register, login, logout, password reset.

use axum::{
    extract::{Json, State},
    http::{header::SET_COOKIE, HeaderValue, StatusCode},
    response::IntoResponse,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use chrono::Utc;
use std::sync::Arc;

use crate::AppState;
use cn_core::auth::{AuthUtils, SignedToken};
use cn_core::entities::user;
use cn_core::entities::user::Entity as UserEntity;

const AUTH_COOKIE_NAME: &str = "auth_token";
const COOKIE_MAX_AGE_SECS: i64 = 60 * 60 * 24 * 30;  // 30 days
/// Name of the DRF-style HttpOnly cookie. The `__Host-` prefix is enforced
/// by browsers and is part of the cookie name; mirrors the Django backend
/// (see `backend/smsly/middleware.py` for the same name).
const DRF_COOKIE_NAME: &str = "__Host-smsly_token";
const DRF_COOKIE_MAX_AGE_SECS: i64 = 60 * 60 * 24;  // 1 day, matches JWT exp

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

/// Build the `__Host-smsly_token` HttpOnly cookie carrying the DRF token.
/// `__Host-` prefix requires `Secure` and `Path=/`; the browser will reject
/// the cookie if either is missing, so we always emit both.
fn build_drf_cookie(drf_token: &str) -> String {
    format!(
        "{}={}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age={}",
        DRF_COOKIE_NAME, drf_token, DRF_COOKIE_MAX_AGE_SECS
    )
}

/// Build a `Max-Age=0` cookie value that clears the `__Host-smsly_token`
/// cookie on the client.
fn clear_drf_cookie() -> String {
    format!(
        "{}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
        DRF_COOKIE_NAME
    )
}

fn cookie_header(cookie: &str) -> Result<HeaderValue, (StatusCode, String)> {
    HeaderValue::from_str(cookie)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}

#[derive(Debug, Deserialize)]
pub struct RegisterBody {
    pub username: String,
    pub email: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct AuthResponse {
    pub user_id: i32,
    pub username: String,
    pub email: String,
    /// JWT (HS256, 24h) — same value as `access_token`, kept under the
    /// `token` key for parity with the Django backend's `obtain_auth_token`
    /// response shape (`{ "token": "<hex>" }`).
    pub token: String,
    /// DRF-style 40-char hex token. Also set as the `__Host-smsly_token`
    /// HttpOnly cookie on the response. Mirrors the `key` field in DRF's
    /// `rest_framework.authtoken.models.Token`.
    pub drf_token: String,
    /// Nested user object matching Django's `User` serializer keys.
    pub user: AuthUserPayload,
    pub access_token: String,    // also set as HttpOnly cookie
    pub refresh_token: String,
    pub expires_in: i64,
}

#[derive(Debug, Serialize)]
pub struct AuthUserPayload {
    pub id: i32,
    pub username: String,
    pub email: String,
    pub is_active: bool,
}

pub async fn register(
    State(state): State<Arc<AppState>>,
    Json(body): Json<RegisterBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    // Check username uniqueness
    if UserEntity::find()
        .filter(user::Column::Username.eq(&body.username))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .is_some()
    {
        return Err((StatusCode::CONFLICT, "username already taken".to_string()));
    }
    // Check email uniqueness
    if UserEntity::find()
        .filter(user::Column::Email.eq(&body.email))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .is_some()
    {
        return Err((StatusCode::CONFLICT, "email already registered".to_string()));
    }
    // Hash password (Argon2)
    let password_hash = AuthUtils::hash_password(&body.password)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    // Create user
    let new_user = user::ActiveModel {
        username: Set(body.username.clone()),
        email: Set(body.email.clone()),
        password: Set(password_hash),
        is_superuser: Set(false),
        is_staff: Set(false),
        is_active: Set(true),
        date_joined: Set(Utc::now().into()),
        ..Default::default()
    };
    let inserted = new_user.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    // Generate tokens (JWT for backward compat, DRF token for parity with Django)
    let pair = SignedToken::issue(inserted.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let refresh_token = AuthUtils::create_jwt(inserted.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    state.drf_tokens.register(inserted.id, &pair.drf_token);
    let secure = !state.config.debug;
    let jwt_cookie = build_auth_cookie(&pair.jwt, secure);
    let drf_cookie = build_drf_cookie(&pair.drf_token);
    let response = AuthResponse {
        user_id: inserted.id,
        username: inserted.username.clone(),
        email: inserted.email.clone(),
        token: pair.jwt.clone(),
        drf_token: pair.drf_token.clone(),
        user: AuthUserPayload {
            id: inserted.id,
            username: inserted.username,
            email: inserted.email,
            is_active: inserted.is_active,
        },
        access_token: pair.jwt,
        refresh_token,
        expires_in: DRF_COOKIE_MAX_AGE_SECS,
    };
    Ok((
        StatusCode::CREATED,
        [
            (SET_COOKIE, cookie_header(&jwt_cookie)?),
            (SET_COOKIE, cookie_header(&drf_cookie)?),
        ],
        Json(response),
    ))
}

#[derive(Debug, Deserialize)]
pub struct LoginBody {
    pub username: String,
    pub password: String,
}

pub async fn login(
    State(state): State<Arc<AppState>>,
    Json(body): Json<LoginBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    // Find user by username
    let u = UserEntity::find()
        .filter(user::Column::Username.eq(&body.username))
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::UNAUTHORIZED, "invalid credentials".to_string()))?;
    if !u.is_active {
        return Err((StatusCode::FORBIDDEN, "account is inactive".to_string()));
    }
    // Verify password (supports Argon2, PBKDF2, bcrypt via P6's auth)
    let is_valid = AuthUtils::verify_password(&body.password, &u.password)
        .unwrap_or(false);
    if !is_valid {
        return Err((StatusCode::UNAUTHORIZED, "invalid credentials".to_string()));
    }
    // Rehash legacy password in background
    if AuthUtils::needs_rehash(&u.password) {
        let db_state = state.clone();
        let pw = body.password.clone();
        let user_id = u.id;
        tokio::spawn(async move {
            if let Ok(new_hash) = AuthUtils::rehash_to_argon2(&pw) {
                if let Ok(Some(model)) = UserEntity::find_by_id(user_id).one(&db_state.db).await {
                    let mut active: user::ActiveModel = model.into();
                    active.password = Set(new_hash);
                    let _ = active.update(&db_state.db).await;
                }
            }
        });
    }
    let pair = SignedToken::issue(u.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let refresh_token = AuthUtils::create_jwt(u.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    state.drf_tokens.register(u.id, &pair.drf_token);
    let secure = !state.config.debug;
    let jwt_cookie = build_auth_cookie(&pair.jwt, secure);
    let drf_cookie = build_drf_cookie(&pair.drf_token);
    let response = AuthResponse {
        user_id: u.id,
        username: u.username.clone(),
        email: u.email.clone(),
        token: pair.jwt.clone(),
        drf_token: pair.drf_token.clone(),
        user: AuthUserPayload {
            id: u.id,
            username: u.username,
            email: u.email,
            is_active: u.is_active,
        },
        access_token: pair.jwt,
        refresh_token,
        expires_in: DRF_COOKIE_MAX_AGE_SECS,
    };
    Ok((
        StatusCode::OK,
        [
            (SET_COOKIE, cookie_header(&jwt_cookie)?),
            (SET_COOKIE, cookie_header(&drf_cookie)?),
        ],
        Json(response),
    ))
}

pub async fn logout() -> impl IntoResponse {
    let mut jwt_cookie = format!("{}=deleted", AUTH_COOKIE_NAME);
    jwt_cookie.push_str("; HttpOnly; SameSite=Strict; Max-Age=0; Path=/");
    let drf_cookie = clear_drf_cookie();
    (
        StatusCode::OK,
        [
            (SET_COOKIE, HeaderValue::from_str(&jwt_cookie).unwrap()),
            (SET_COOKIE, HeaderValue::from_str(&drf_cookie).unwrap()),
        ],
        Json(serde_json::json!({ "status": "logged out" })),
    )
}

#[derive(Debug, Deserialize)]
pub struct RefreshBody {
    pub refresh_token: String,
}

pub async fn refresh_token(
    State(state): State<Arc<AppState>>,
    Json(body): Json<RefreshBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let claims = AuthUtils::decode_jwt(&body.refresh_token, &state.config.secret_key)
        .map_err(|_| (StatusCode::UNAUTHORIZED, "invalid refresh token".to_string()))?;
    // Issue a new (jwt, drf_token) pair on every refresh
    let pair = SignedToken::issue(claims.sub, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    state.drf_tokens.register(claims.sub, &pair.drf_token);
    let secure = !state.config.debug;
    let jwt_cookie = build_auth_cookie(&pair.jwt, secure);
    let drf_cookie = build_drf_cookie(&pair.drf_token);
    Ok((
        StatusCode::OK,
        [
            (SET_COOKIE, cookie_header(&jwt_cookie)?),
            (SET_COOKIE, cookie_header(&drf_cookie)?),
        ],
        Json(AuthResponse {
            user_id: claims.sub,
            username: String::new(),  // refreshed tokens don't echo username
            email: String::new(),
            token: pair.jwt.clone(),
            drf_token: pair.drf_token.clone(),
            user: AuthUserPayload {
                id: claims.sub,
                username: String::new(),
                email: String::new(),
                is_active: true,
            },
            access_token: pair.jwt,
            refresh_token: body.refresh_token,
            expires_in: DRF_COOKIE_MAX_AGE_SECS,
        }),
    ))
}

#[derive(Debug, Deserialize)]
pub struct PasswordChangeBody {
    pub old_password: String,
    pub new_password: String,
}

pub async fn change_password(
    State(state): State<Arc<AppState>>,
    auth: crate::middleware::AuthUser,
    Json(body): Json<PasswordChangeBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let u = UserEntity::find_by_id(auth.id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "user not found".to_string()))?;
    if !AuthUtils::verify_password(&body.old_password, &u.password).unwrap_or(false) {
        return Err((StatusCode::UNAUTHORIZED, "old password incorrect".to_string()));
    }
    let new_hash = AuthUtils::hash_password(&body.new_password)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let mut active: user::ActiveModel = u.into();
    active.password = Set(new_hash);
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "ok" })))
}

pub async fn me(
    State(state): State<Arc<AppState>>,
    auth: crate::middleware::AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let u = UserEntity::find_by_id(auth.id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "user not found".to_string()))?;
    Ok(Json(serde_json::json!({
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "is_superuser": u.is_superuser,
        "is_staff": u.is_staff,
        "is_active": u.is_active,
        "date_joined": u.date_joined,
    })))
}
