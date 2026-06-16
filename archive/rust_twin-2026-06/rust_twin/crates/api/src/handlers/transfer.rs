//! Transfer HTTP handlers (server-to-server migration).

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use std::sync::Arc;
use uuid::Uuid;
use chrono::Utc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::transfer_log;
use cn_core::entities::transfer_log::Entity as TransferLogEntity;

pub async fn list_transfers(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let transfers = TransferLogEntity::find().all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(transfers))
}

pub async fn get_transfer(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TransferLogEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "transfer not found".to_string()))?;
    Ok(Json(t))
}

pub async fn cancel_transfer(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TransferLogEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "transfer not found".to_string()))?;
    let mut active: transfer_log::ActiveModel = t.into();
    active.status = Set("cancelled".to_string());
    active.completed_at = Set(Some(Utc::now().into()));
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "cancelled" })))
}
