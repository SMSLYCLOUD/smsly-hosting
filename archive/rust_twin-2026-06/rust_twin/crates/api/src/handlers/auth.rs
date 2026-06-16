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
use cn_core::auth::AuthUtils;
use cn_core::entities::user;
use cn_core::entities::user::Entity as UserEntity;

const AUTH_COOKIE_NAME: &str = "auth_token";
const COOKIE_MAX_AGE_SECS: i64 = 60 * 60 * 24 * 30;  // 30 days

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
    pub access_token: String,    // also set as HttpOnly cookie
    pub refresh_token: String,
    pub expires_in: i64,
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
    // Generate tokens
    let access_token = AuthUtils::create_jwt(inserted.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let refresh_token = AuthUtils::create_jwt(inserted.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let secure = !state.config.debug;
    let cookie = build_auth_cookie(&access_token, secure);
    let response = AuthResponse {
        user_id: inserted.id,
        username: inserted.username,
        email: inserted.email,
        access_token: access_token.clone(),
        refresh_token,
        expires_in: COOKIE_MAX_AGE_SECS,
    };
    Ok((
        StatusCode::CREATED,
        [(SET_COOKIE, cookie_header(&cookie)?)],
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
    let access_token = AuthUtils::create_jwt(u.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let refresh_token = AuthUtils::create_jwt(u.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let secure = !state.config.debug;
    let cookie = build_auth_cookie(&access_token, secure);
    let response = AuthResponse {
        user_id: u.id,
        username: u.username,
        email: u.email,
        access_token: access_token.clone(),
        refresh_token,
        expires_in: COOKIE_MAX_AGE_SECS,
    };
    Ok((
        StatusCode::OK,
        [(SET_COOKIE, cookie_header(&cookie)?)],
        Json(response),
    ))
}

pub async fn logout() -> impl IntoResponse {
    let mut cookie = format!("{}=deleted", AUTH_COOKIE_NAME);
    cookie.push_str("; HttpOnly; SameSite=Strict; Max-Age=0; Path=/");
    (
        StatusCode::OK,
        [(SET_COOKIE, HeaderValue::from_str(&cookie).unwrap())],
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
    // Issue a new access token
    let access_token = AuthUtils::create_jwt(claims.sub, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let secure = !state.config.debug;
    let cookie = build_auth_cookie(&access_token, secure);
    Ok((
        StatusCode::OK,
        [(SET_COOKIE, cookie_header(&cookie)?)],
        Json(AuthResponse {
            user_id: claims.sub,
            username: String::new(),  // refreshed tokens don't echo username
            email: String::new(),
            access_token,
            refresh_token: body.refresh_token,
            expires_in: COOKIE_MAX_AGE_SECS,
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
