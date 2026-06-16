//! Admin HTTP handlers — user list, system stats, kill switch, audit log search.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, PaginatorTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use chrono::Utc;
use std::sync::Arc;
use uuid::Uuid;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::user;
use cn_core::entities::user::Entity as UserEntity;
use cn_core::entities::deployment;
use cn_core::entities::service;
use cn_core::entities::project;

#[derive(Debug, Serialize)]
pub struct AdminUserResponse {
    pub id: i32,
    pub username: String,
    pub email: String,
    pub is_active: bool,
    pub is_staff: bool,
    pub is_superuser: bool,
    pub date_joined: chrono::DateTime<Utc>,
    pub last_login: Option<chrono::DateTime<Utc>>,
    pub project_count: i64,
    pub service_count: i64,
    pub deployment_count: i64,
}

fn require_superuser(auth: &AuthUser) -> Result<(), (StatusCode, String)> {
    // In a real implementation, check auth.is_superuser
    // For now, check via the user entity
    Err((StatusCode::FORBIDDEN, "admin access required".to_string()))
}

pub async fn list_users(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Query(q): Query<ListUsersQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    require_superuser(&auth)?;
    let mut query = UserEntity::find();
    if let Some(search) = q.search {
        query = query.filter(user::Column::Username.contains(&search));
    }
    let page = q.page.unwrap_or(1).max(1);
    let per_page = q.per_page.unwrap_or(50).clamp(1, 200);
    let paginator = query.paginate(&state.db, per_page);
    let total = paginator.num_items().await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let users = paginator.fetch_page(page - 1).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let mut resp = Vec::with_capacity(users.len());
    for u in users {
        let owned_projects = project::Entity::find()
            .filter(project::Column::OwnerId.eq(u.id))
            .all(&state.db).await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        let project_count = owned_projects.len() as i64;
        let owned_project_ids: std::collections::HashSet<Uuid> = owned_projects.iter().map(|p| p.id).collect();
        let all_services = service::Entity::find()
            .all(&state.db).await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        let owned_service_ids: std::collections::HashSet<Uuid> = all_services.iter()
            .filter(|s| owned_project_ids.contains(&s.project_id))
            .map(|s| s.id)
            .collect();
        let service_count = owned_service_ids.len() as i64;
        let all_deployments = deployment::Entity::find()
            .all(&state.db).await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        let deployment_count = all_deployments.iter()
            .filter(|d| owned_service_ids.contains(&d.service_id))
            .count() as i64;
        resp.push(AdminUserResponse {
            id: u.id,
            username: u.username,
            email: u.email,
            is_active: u.is_active,
            is_staff: u.is_staff,
            is_superuser: u.is_superuser,
            date_joined: u.date_joined.with_timezone(&Utc),
            last_login: None,
            project_count,
            service_count,
            deployment_count,
        });
    }
    Ok(Json(serde_json::json!({
        "items": resp,
        "total": total,
        "page": page,
        "per_page": per_page,
    })))
}

#[derive(Debug, Deserialize)]
pub struct ListUsersQuery {
    pub search: Option<String>,
    pub page: Option<u64>,
    pub per_page: Option<u64>,
}

pub async fn get_user(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(id): Path<i32>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    require_superuser(&auth)?;
    let u = UserEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "user not found".to_string()))?;
    Ok(Json(serde_json::json!({
        "id": u.id, "username": u.username, "email": u.email,
        "is_active": u.is_active, "is_staff": u.is_staff, "is_superuser": u.is_superuser,
        "date_joined": u.date_joined,
    })))
}

#[derive(Debug, Deserialize)]
pub struct UpdateUserBody {
    pub is_active: Option<bool>,
    pub is_staff: Option<bool>,
    pub is_superuser: Option<bool>,
}

pub async fn update_user(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
    Path(id): Path<i32>,
    Json(body): Json<UpdateUserBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    require_superuser(&auth)?;
    let u = UserEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "user not found".to_string()))?;
    use sea_orm::ActiveModelTrait;
    let mut active: user::ActiveModel = u.into();
    if let Some(b) = body.is_active { active.is_active = Set(b); }
    if let Some(b) = body.is_staff { active.is_staff = Set(b); }
    if let Some(b) = body.is_superuser { active.is_superuser = Set(b); }
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "updated" })))
}

#[derive(Debug, Serialize)]
pub struct SystemStats {
    pub user_count: i64,
    pub project_count: i64,
    pub service_count: i64,
    pub deployment_count: i64,
    pub running_deployments: i64,
    pub failed_deployments_last_24h: i64,
    pub total_backups: i64,
    pub total_addons: i64,
    pub db_size_mb: Option<f64>,
    pub uptime_secs: u64,
}

pub async fn system_stats(
    State(state): State<Arc<AppState>>,
    auth: AuthUser,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    require_superuser(&auth)?;
    let user_count = UserEntity::find().all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    let project_count = project::Entity::find().all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    let service_count = service::Entity::find().all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    let deployment_count = deployment::Entity::find().all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    let running_deployments = deployment::Entity::find()
        .filter(deployment::Column::Status.eq("RUNNING"))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    let cutoff = Utc::now() - chrono::Duration::hours(24);
    let failed_deployments_last_24h = deployment::Entity::find()
        .filter(deployment::Column::Status.eq("DEPLOY_FAILED"))
        .filter(deployment::Column::CreatedAt.gt(cutoff))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?.len() as i64;
    // In a real implementation, query the backup_record and addon tables
    Ok(Json(SystemStats {
        user_count,
        project_count,
        service_count,
        deployment_count,
        running_deployments,
        failed_deployments_last_24h,
        total_backups: 0,  // stub
        total_addons: 0,   // stub
        db_size_mb: None,
        uptime_secs: 0,    // would need a static start time
    }))
}

#[derive(Debug, Serialize)]
pub struct AuditLogEntry {
    pub id: i64,
    pub timestamp: chrono::DateTime<Utc>,
    pub actor_id: Option<i32>,
    pub actor_username: Option<String>,
    pub action: String,
    pub target_type: String,
    pub target_id: String,
    pub ip_address: Option<String>,
    pub metadata: Option<serde_json::Value>,
}

/// Stub — in production, this would query the Django-style audit log table.
/// The audit_log entity doesn't exist in the rust_twin yet; this returns
/// an empty list with a note.
pub async fn search_audit_log(
    State(_state): State<Arc<AppState>>,
    _auth: AuthUser,
    Query(_q): Query<AuditLogQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    Ok(Json(serde_json::json!({
        "items": [],
        "total": 0,
        "note": "audit log table not yet migrated to rust_twin",
    })))
}

#[derive(Debug, Deserialize)]
pub struct AuditLogQuery {
    pub actor_id: Option<i32>,
    pub action: Option<String>,
    pub target_type: Option<String>,
    pub since: Option<chrono::DateTime<Utc>>,
    pub until: Option<chrono::DateTime<Utc>>,
    pub page: Option<u64>,
    pub per_page: Option<u64>,
}

/// Kill switch — pause all deployments and addons globally.
/// In production, this sets a feature flag in Redis that all workers check
/// before starting new work.
pub async fn kill_switch(
    State(_state): State<Arc<AppState>>,
    auth: AuthUser,
    Json(body): Json<KillSwitchBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    require_superuser(&auth)?;
    // In a real implementation: state.redis.set("smsly:kill_switch", "1").await
    Ok(Json(serde_json::json!({
        "enabled": body.enabled,
        "reason": body.reason,
        "actor": auth.id,
    })))
}

#[derive(Debug, Deserialize)]
pub struct KillSwitchBody {
    pub enabled: bool,
    pub reason: String,
}
