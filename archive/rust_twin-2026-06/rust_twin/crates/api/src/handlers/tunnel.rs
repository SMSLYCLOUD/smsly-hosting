//! Tunnel HTTP handlers.

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

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::tunnel;
use cn_core::entities::tunnel::Entity as TunnelEntity;

#[derive(Debug, Serialize)]
pub struct TunnelResponse {
    pub id: Uuid,
    pub service_id: Uuid,
    pub local_port: i32,
    pub public_subdomain: String,
    pub public_port: i32,
    pub protocol: String,
    pub status: String,
    pub connection_count: i32,
    pub bytes_in: i64,
    pub bytes_out: i64,
    pub last_connected_at: Option<chrono::DateTime<Utc>>,
}

impl From<tunnel::Model> for TunnelResponse {
    fn from(t: tunnel::Model) -> Self {
        Self {
            id: t.id,
            service_id: t.service_id,
            local_port: t.local_port,
            public_subdomain: t.public_subdomain,
            public_port: t.public_port,
            protocol: t.protocol,
            status: t.status,
            connection_count: t.connection_count,
            bytes_in: t.bytes_in,
            bytes_out: t.bytes_out,
            last_connected_at: t.last_connected_at.map(|dt| dt.with_timezone(&Utc)),
        }
    }
}

pub async fn list_tunnels(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let tunnels = TunnelEntity::find()
        .filter(tunnel::Column::ServiceId.eq(service_id))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<TunnelResponse> = tunnels.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn get_tunnel(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TunnelEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "tunnel not found".to_string()))?;
    Ok(Json(TunnelResponse::from(t)))
}

pub async fn disable_tunnel(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let t = TunnelEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "tunnel not found".to_string()))?;
    let mut active: tunnel::ActiveModel = t.into();
    active.status = Set("inactive".to_string());
    active.updated_at = Set(Utc::now().into());
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "inactive" })))
}
