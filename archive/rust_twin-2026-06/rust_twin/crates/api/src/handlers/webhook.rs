//! Webhook HTTP handlers.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;
use chrono::Utc;

use crate::{AppState, middleware::AuthUser};
use crate::services::webhooks::dispatcher::{WebhookDispatcher, WebhookEvent};
use crate::services::webhooks::ssrf_guard::validate_url;
use cn_core::entities::webhook;
use cn_core::entities::webhook::Entity as WebhookEntity;

#[derive(Debug, Serialize)]
pub struct WebhookResponse {
    pub id: Uuid,
    pub url: String,
    pub events: String,
    pub is_active: bool,
    pub last_triggered_at: Option<chrono::DateTime<Utc>>,
    pub last_response_code: Option<i32>,
    pub failure_count: i32,
    pub created_at: chrono::DateTime<Utc>,
}

impl From<webhook::Model> for WebhookResponse {
    fn from(w: webhook::Model) -> Self {
        Self {
            id: w.id,
            url: w.url,
            events: w.events,
            is_active: w.is_active,
            last_triggered_at: w.last_triggered_at.map(|dt| dt.with_timezone(&Utc)),
            last_response_code: w.last_response_code,
            failure_count: w.failure_count,
            created_at: w.created_at.with_timezone(&Utc),
        }
    }
}

pub async fn list_webhooks(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let webhooks = WebhookEntity::find()
        .filter(webhook::Column::UserId.eq(auth.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<WebhookResponse> = webhooks.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

#[derive(Debug, Deserialize)]
pub struct CreateWebhookBody {
    pub url: String,
    pub events: Vec<String>,
    pub service_id: Option<Uuid>,
}

pub async fn create_webhook(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Json(body): Json<CreateWebhookBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    validate_url(&body.url)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("URL rejected: {}", e)))?;
    let secret = generate_secret();
    let now = Utc::now();
    let new_webhook = webhook::ActiveModel {
        id: Set(Uuid::new_v4()),
        user_id: Set(auth.id),
        service_id: Set(body.service_id),
        url: Set(body.url),
        secret: Set(secret),
        events: Set(serde_json::to_string(&body.events).unwrap_or_default()),
        is_active: Set(true),
        last_triggered_at: Set(None),
        last_response_code: Set(None),
        failure_count: Set(0),
        created_at: Set(now.into()),
        updated_at: Set(now.into()),
    };
    let inserted = new_webhook
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::CREATED, Json(WebhookResponse::from(inserted))))
}

pub async fn delete_webhook(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    WebhookEntity::delete_by_id(id)
        .exec(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn test_webhook(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let w = WebhookEntity::find_by_id(id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "webhook not found".to_string()))?;
    let dispatcher = WebhookDispatcher::new_for_test();
    let event = WebhookEvent {
        event_type: "test".to_string(),
        timestamp: Utc::now(),
        data: serde_json::json!({ "message": "test from rust_twin" }),
    };
    let result = dispatcher.dispatch(&w, &event).await;
    let status_str = if result.is_ok() { "ok" } else { "failed" };
    let err_str = result.err().map(|e| e.to_string());
    Ok(Json(serde_json::json!({
        "result": status_str,
        "error": err_str,
    })))
}

fn generate_secret() -> String {
    Uuid::new_v4().simple().to_string()
}
