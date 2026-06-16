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
            // Return a mocked default "community" response
            let default_license = platform_license::Model {
                id: 0,
                license_key: "".to_string(),
                tier: "community".to_string(),
                license_data: "".to_string(),
                is_valid: true,
                last_validated: None,
                validation_error: "".to_string(),
                licensed_to: "".to_string(),
                instance_id: "".to_string(),
                expires_at: None,
                max_services: 3,
                max_team_members: 1,
                payment_provider: "".to_string(),
                subscription_id: "".to_string(),
                created_at: chrono::Utc::now().into(),
                updated_at: chrono::Utc::now().into(),
            };
            Ok(Json(default_license))
        }
    }
}

#[derive(serde::Deserialize)]
pub struct UpgradePayload {
    pub target_tier: String, // "pro" or "enterprise"
    pub payment_id: String,  // Mocked ID from external provider
}

// Simulate a successful webhook payment processing
pub async fn upgrade_license(
    State(state): State<Arc<AppState>>,
    _auth_user: AuthUser, // Ensure only authenticated admins can trigger this
    Json(payload): Json<UpgradePayload>,
) -> Result<Json<platform_license::Model>, (StatusCode, String)> {

    let target_tier = payload.target_tier.to_lowercase();
    if target_tier != "pro" && target_tier != "enterprise" {
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
            model.subscription_id = Set(payload.payment_id.clone());
            model.expires_at = Set(Some(expiration.into()));
            model.is_valid = Set(true);
            model.updated_at = Set(chrono::Utc::now().into());
            model
        }
        None => {
            platform_license::ActiveModel {
                license_key: Set("".to_string()),
                tier: Set(target_tier),
                license_data: Set("".to_string()),
                is_valid: Set(true),
                last_validated: Set(None),
                validation_error: Set("".to_string()),
                licensed_to: Set("".to_string()),
                instance_id: Set("".to_string()),
                expires_at: Set(Some(expiration.into())),
                max_services: Set(100),
                max_team_members: Set(10),
                payment_provider: Set("stripe".to_string()),
                subscription_id: Set(payload.payment_id.clone()),
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
        license_key: saved.license_key.unwrap(),
        tier: saved.tier.unwrap(),
        license_data: saved.license_data.unwrap(),
        is_valid: saved.is_valid.unwrap(),
        last_validated: saved.last_validated.unwrap(),
        validation_error: saved.validation_error.unwrap(),
        licensed_to: saved.licensed_to.unwrap(),
        instance_id: saved.instance_id.unwrap(),
        expires_at: saved.expires_at.unwrap(),
        max_services: saved.max_services.unwrap(),
        max_team_members: saved.max_team_members.unwrap(),
        payment_provider: saved.payment_provider.unwrap(),
        subscription_id: saved.subscription_id.unwrap(),
        created_at: saved.created_at.unwrap(),
        updated_at: saved.updated_at.unwrap(),
    };

    Ok(Json(result))
}