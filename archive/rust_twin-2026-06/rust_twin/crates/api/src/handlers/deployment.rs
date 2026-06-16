//! Deployment HTTP handlers — mirrors DeploymentViewSet in views.py.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, PaginatorTrait, QueryFilter, QueryOrder, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;
use chrono::Utc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::deployment;
use cn_core::entities::deployment::Entity as DeploymentEntity;
use cn_core::deployment_status::DeploymentStatus;

#[derive(Debug, Serialize)]
pub struct DeploymentResponse {
    pub id: Uuid,
    pub service_id: Uuid,
    pub commit_hash: String,
    pub status: String,
    pub status_enum: String,            // parsed from the string column
    pub is_rollback: bool,
    pub requester_id: Option<i32>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub finished_at: Option<chrono::DateTime<chrono::Utc>>,
}

impl From<deployment::Model> for DeploymentResponse {
    fn from(m: deployment::Model) -> Self {
        let status_enum = DeploymentStatus::from_str(&m.status)
            .map(|s| s.as_str().to_string())
            .unwrap_or_else(|| m.status.clone());
        Self {
            id: m.id,
            service_id: m.service_id,
            commit_hash: m.commit_hash,
            status: m.status,
            status_enum,
            is_rollback: m.is_rollback,
            requester_id: m.requester_id,
            created_at: m.created_at.with_timezone(&Utc),
            started_at: m.started_at.map(|dt| dt.with_timezone(&Utc)),
            finished_at: m.finished_at.map(|dt| dt.with_timezone(&Utc)),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct ListQuery {
    pub service_id: Option<Uuid>,
    pub status: Option<String>,
    pub page: Option<u64>,
    pub per_page: Option<u64>,
}

pub async fn list_deployments(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Query(q): Query<ListQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let mut query = DeploymentEntity::find();
    if let Some(svc) = q.service_id {
        query = query.filter(deployment::Column::ServiceId.eq(svc));
    }
    if let Some(s) = q.status {
        query = query.filter(deployment::Column::Status.eq(s));
    }
    let page = q.page.unwrap_or(1).max(1);
    let per_page = q.per_page.unwrap_or(20).clamp(1, 100);
    let paginator = query
        .order_by_desc(deployment::Column::CreatedAt)
        .paginate(&state.db, per_page);
    let total = paginator.num_items().await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let items = paginator.fetch_page(page - 1).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<DeploymentResponse> = items.into_iter().map(Into::into).collect();
    Ok(Json(serde_json::json!({
        "items": resp,
        "page": page,
        "per_page": per_page,
        "total": total,
    })))
}

pub async fn get_deployment(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let d = DeploymentEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "deployment not found".to_string()))?;
    Ok(Json(DeploymentResponse::from(d)))
}

#[derive(Debug, Deserialize)]
pub struct TriggerDeploymentBody {
    pub service_id: Uuid,
    pub commit_hash: String,
}

pub async fn trigger_deployment(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Json(body): Json<TriggerDeploymentBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let now = Utc::now();
    let new_dep = deployment::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(body.service_id),
        commit_hash: Set(body.commit_hash),
        status: Set(DeploymentStatus::Queued.as_str().to_string()),
        is_rollback: Set(false),
        started_at: Set(None),
        finished_at: Set(None),
        requester_id: Set(Some(auth.id)),
        created_at: Set(now.into()),
    };
    let inserted = new_dep.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::ACCEPTED, Json(DeploymentResponse::from(inserted))))
}

pub async fn cancel_deployment(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let d = DeploymentEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "deployment not found".to_string()))?;
    let mut active: deployment::ActiveModel = d.into();
    active.status = Set("STOPPED".to_string());
    active.finished_at = Set(Some(Utc::now().into()));
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "cancelled" })))
}

pub async fn retry_deployment(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let original = DeploymentEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "deployment not found".to_string()))?;
    let now = Utc::now();
    let retry = deployment::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(original.service_id),
        commit_hash: Set(original.commit_hash.clone()),
        status: Set(DeploymentStatus::Queued.as_str().to_string()),
        is_rollback: Set(false),
        started_at: Set(None),
        finished_at: Set(None),
        requester_id: Set(original.requester_id),
        created_at: Set(now.into()),
    };
    let inserted = retry.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::ACCEPTED, Json(DeploymentResponse::from(inserted))))
}

pub async fn rollback_deployment(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let original = DeploymentEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "deployment not found".to_string()))?;
    let now = Utc::now();
    let rollback = deployment::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(original.service_id),
        commit_hash: Set(original.commit_hash),
        status: Set(DeploymentStatus::RolledBack.as_str().to_string()),
        is_rollback: Set(true),
        started_at: Set(Some(now.into())),
        finished_at: Set(Some(now.into())),
        requester_id: Set(original.requester_id),
        created_at: Set(now.into()),
    };
    let inserted = rollback.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::ACCEPTED, Json(DeploymentResponse::from(inserted))))
}
