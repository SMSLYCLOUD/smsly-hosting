use axum::{extract::{Path, State}, http::StatusCode, response::IntoResponse, routing::{delete, get, post}, Json, Router};
use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, QueryFilter, Set};
use serde::Deserialize;
use std::sync::Arc;

use super::dispatcher::WebhookDispatcher;
use cn_core::entities::webhook;

#[derive(Clone)]
pub struct AppState {
    pub db: Arc<DatabaseConnection>,
    pub dispatcher: Arc<WebhookDispatcher>,
}

pub fn create_router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/webhooks", post(create_webhook).get(list_webhooks))
        .route("/api/v1/webhooks/:id", get(get_webhook).delete(delete_webhook))
        .route("/api/v1/webhooks/:id/test", post(test_webhook))
}

#[derive(Deserialize)]
pub struct CreateWebhookBody {
    pub url: String,
    pub events: Vec<String>,
    pub service_id: Option<uuid::Uuid>,
}

pub async fn create_webhook(
    State(_state): State<AppState>,
    Json(_body): Json<CreateWebhookBody>,
) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}

pub async fn list_webhooks(State(_state): State<AppState>) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}

pub async fn get_webhook(State(_state): State<AppState>, Path(_id): Path<uuid::Uuid>) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}

pub async fn delete_webhook(State(_state): State<AppState>, Path(_id): Path<uuid::Uuid>) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}

pub async fn test_webhook(State(_state): State<AppState>, Path(_id): Path<uuid::Uuid>) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}
