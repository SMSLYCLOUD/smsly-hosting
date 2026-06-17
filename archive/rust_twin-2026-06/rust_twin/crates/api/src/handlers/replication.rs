//! Replication HTTP handlers — manage cross-region warm standbys.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::services::replication as svc;

#[derive(Debug, Serialize)]
pub struct ReplicationResponse {
    pub id: Uuid,
    pub service_id: Uuid,
    pub target_region: String,
    pub status: String,
    pub lag_seconds: i32,
    pub last_sync_at: Option<chrono::DateTime<chrono::Utc>>,
}

impl From<svc::ReplicationTarget> for ReplicationResponse {
    fn from(t: svc::ReplicationTarget) -> Self {
        Self {
            id: t.id,
            service_id: t.service_id,
            target_region: t.target_region,
            status: t.status,
            lag_seconds: t.lag_seconds,
            last_sync_at: t.last_sync_at,
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateReplicationBody {
    pub target_region: String,
}

pub async fn list_replication_targets(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let targets = svc::list_replication_targets(&state.db, service_id)
        .await
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, e.to_string()))?;
    let resp: Vec<ReplicationResponse> = targets.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn create_replication_target(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
    Json(body): Json<CreateReplicationBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let target = svc::create_replication_target(&state.db, service_id, body.target_region)
        .await
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, e.to_string()))?;
    Ok((StatusCode::ACCEPTED, Json(ReplicationResponse::from(target))))
}

pub async fn delete_replication_target(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    svc::delete_replication_target(&state.db, id)
        .await
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}
