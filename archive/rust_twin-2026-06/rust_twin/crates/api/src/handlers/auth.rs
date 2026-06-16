use axum::{
    extract::State,
    http::StatusCode,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::AppState;
use cn_core::auth::AuthUtils;
use cn_core::entities::user;

#[derive(Deserialize)]
pub struct RegisterRequest {
    pub username: String,
    pub email: String,
    pub password: String,
}

#[derive(Serialize)]
pub struct AuthResponse {
    pub token: String,
    pub user_id: i32,
    pub username: String,
}

pub async fn register(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<RegisterRequest>,
) -> Result<(StatusCode, Json<AuthResponse>), (StatusCode, String)> {
    // 1. Check if user exists
    let existing = user::Entity::find()
        .filter(user::Column::Username.eq(&payload.username))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if existing.is_some() {
        return Err((StatusCode::BAD_REQUEST, "Username already exists".to_string()));
    }

    // 2. Hash password
    let password_hash = AuthUtils::hash_password(&payload.password)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // 3. Create active model
    let new_user = user::ActiveModel {
        username: Set(payload.username.clone()),
        email: Set(payload.email.clone()),
        password: Set(password_hash),
        is_superuser: Set(false),
        is_staff: Set(false),
        is_active: Set(true),
        date_joined: Set(chrono::Utc::now().into()),
        ..Default::default()
    };

    // 4. Insert into database
    let inserted = new_user
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // 5. Generate JWT
    let token = AuthUtils::create_jwt(inserted.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok((
        StatusCode::CREATED,
        Json(AuthResponse {
            token,
            user_id: inserted.id,
            username: inserted.username,
        }),
    ))
}

#[derive(Deserialize)]
pub struct LoginRequest {
    pub username: String,
    pub password: String,
}

pub async fn login(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<LoginRequest>,
) -> Result<Json<AuthResponse>, (StatusCode, String)> {
    // 1. Fetch user by username
    let user_model = user::Entity::find()
        .filter(user::Column::Username.eq(&payload.username))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let user_model = match user_model {
        Some(u) => u,
        None => return Err((StatusCode::BAD_REQUEST, "Invalid credentials".to_string())),
    };

    // 2. Verify password — supports Argon2 (rust_twin default) and all
    //    Django hash formats (Argon2, PBKDF2-SHA256, PBKDF2-SHA1, bcrypt_sha256)
    //    so accounts that originated in the Django backend can still log in.
    let is_valid = AuthUtils::verify_password(&payload.password, &user_model.password)
        .unwrap_or(false);

    if !is_valid {
        return Err((StatusCode::BAD_REQUEST, "Invalid credentials".to_string()));
    }

    // 3. Opportunistic re-hash: if the stored hash is using a legacy
    //    algorithm (PBKDF2 family) upgrade it to Argon2 in the background.
    //    The login itself is not blocked by the re-hash.
    if AuthUtils::needs_rehash(&user_model.password) {
        let state_clone = Arc::clone(&state);
        let user_id = user_model.id;
        let password = payload.password.clone();
        tokio::spawn(async move {
            match AuthUtils::rehash_to_argon2(&password) {
                Ok(new_hash) => {
                    if let Ok(Some(u)) = user::Entity::find_by_id(user_id)
                        .one(&state_clone.db)
                        .await
                    {
                        let mut active: user::ActiveModel = u.into();
                        active.password = Set(new_hash);
                        if let Err(e) = active.update(&state_clone.db).await {
                            tracing::warn!(
                                "Failed to upgrade password hash for user {}: {}",
                                user_id,
                                e
                            );
                        }
                    }
                }
                Err(e) => tracing::warn!(
                    "Failed to compute upgraded Argon2 hash for user {}: {}",
                    user_id,
                    e
                ),
            }
        });
    }

    // 4. Generate JWT
    let token = AuthUtils::create_jwt(user_model.id, &state.config.secret_key)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(AuthResponse {
        token,
        user_id: user_model.id,
        username: user_model.username,
    }))
}