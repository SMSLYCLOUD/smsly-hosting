use axum::{
    extract::State,
    http::StatusCode,
    Json,
};
use sea_orm::{ActiveModelTrait, EntityTrait, Set};
use std::sync::Arc;

use cn_core::entities::platform_license;
use crate::{AppState, middleware::AuthUser};

// Fetch the current active license
pub async fn get_license(
    State(state): State<Arc<AppState>>,
    _auth_user: AuthUser, // Protected route
) -> Result<Json<platform_license::Model>, (StatusCode, String)> {

    // There is typically only one PlatformLicense in the DB (Singleton pattern)
    // We grab the first one, or return a default Community fallback if missing
    let license = platform_license::Entity::find()
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    match license {
        Some(l) => Ok(Json(l)),
        None => {
            // Return a mocked default "COMMUNITY" response
            let default_license = platform_license::Model {
                id: 0,
                tier: "COMMUNITY".to_string(),
                customer_id: None,
                subscription_id: None,
                is_active: true,
                expires_at: None,
                encrypted_license_key: None,
                created_at: chrono::Utc::now().into(),
                updated_at: chrono::Utc::now().into(),
            };
            Ok(Json(default_license))
        }
    }
}

#[derive(serde::Deserialize)]
pub struct UpgradePayload {
    pub target_tier: String, // "PRO" or "ENTERPRISE"
    pub payment_id: String,  // Mocked ID from external provider
}

// Simulate a successful webhook payment processing
pub async fn upgrade_license(
    State(state): State<Arc<AppState>>,
    _auth_user: AuthUser, // Ensure only authenticated admins can trigger this
    Json(payload): Json<UpgradePayload>,
) -> Result<Json<platform_license::Model>, (StatusCode, String)> {

    let target_tier = payload.target_tier.to_uppercase();
    if target_tier != "PRO" && target_tier != "ENTERPRISE" {
        return Err((StatusCode::BAD_REQUEST, "Invalid target tier".to_string()));
    }

    // 1. Fetch existing license
    let existing_opt = platform_license::Entity::find()
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // 2. Calculate expiration (30 days from now for PRO)
    let expiration = chrono::Utc::now() + chrono::Duration::days(30);

    // 3. Update or Create
    let active_model = match existing_opt {
        Some(existing) => {
            let mut model: platform_license::ActiveModel = existing.into();
            model.tier = Set(target_tier);
            model.subscription_id = Set(Some(payload.payment_id));
            model.expires_at = Set(Some(expiration.into()));
            model.is_active = Set(true);
            model.updated_at = Set(chrono::Utc::now().into());
            model
        }
        None => {
            platform_license::ActiveModel {
                tier: Set(target_tier),
                customer_id: Set(None),
                subscription_id: Set(Some(payload.payment_id)),
                expires_at: Set(Some(expiration.into())),
                is_active: Set(true),
                created_at: Set(chrono::Utc::now().into()),
                updated_at: Set(chrono::Utc::now().into()),
                ..Default::default()
            }
        }
    };

    let saved = active_model.save(&state.db).await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Database save error: {}", e))
    })?;

    // Conversion back to Model to return JSON
    let result = platform_license::Model {
        id: saved.id.unwrap(),
        tier: saved.tier.unwrap(),
        customer_id: saved.customer_id.unwrap(),
        subscription_id: saved.subscription_id.unwrap(),
        is_active: saved.is_active.unwrap(),
        expires_at: saved.expires_at.unwrap(),
        encrypted_license_key: saved.encrypted_license_key.unwrap(),
        created_at: saved.created_at.unwrap(),
        updated_at: saved.updated_at.unwrap(),
    };

    Ok(Json(result))
}