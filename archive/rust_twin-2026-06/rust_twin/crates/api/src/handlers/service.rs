//! Service HTTP handlers.

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

use crate::{AppState, middleware::AuthUser};
use cn_core::entities::service;
use cn_core::entities::service::Entity as ServiceEntity;
use cn_core::entities::deployment;
use cn_core::entities::environment_variable;
use cn_core::entities::environment_variable::Entity as EnvVarEntity;

#[derive(Debug, Serialize)]
pub struct ServiceResponse {
    pub id: Uuid,
    pub project_id: Uuid,
    pub slug: String,
    pub name: String,
    pub deploy_type: String,
    pub repository_url: Option<String>,
    pub branch: String,
    pub root_directory: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl From<service::Model> for ServiceResponse {
    fn from(m: service::Model) -> Self {
        Self {
            id: m.id,
            project_id: m.project_id,
            slug: m.slug,
            name: m.name,
            deploy_type: m.deploy_type,
            repository_url: m.repository_url,
            branch: m.branch,
            root_directory: Some(m.root_directory),
            created_at: m.created_at.with_timezone(&chrono::Utc),
            updated_at: m.updated_at.with_timezone(&chrono::Utc),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct ListServicesQuery {
    pub project_id: Option<Uuid>,
    pub page: Option<u64>,
    pub per_page: Option<u64>,
}

pub async fn list_services(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Query(q): Query<ListServicesQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let mut query = ServiceEntity::find();
    if let Some(pid) = q.project_id {
        query = query.filter(service::Column::ProjectId.eq(pid));
    }
    let page = q.page.unwrap_or(1).max(1);
    let per_page = q.per_page.unwrap_or(20).clamp(1, 100);
    let paginator = query.paginate(&state.db, per_page);
    let total = paginator.num_items().await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let items = paginator.fetch_page(page - 1).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<ServiceResponse> = items.into_iter().map(Into::into).collect();
    Ok(Json(serde_json::json!({ "items": resp, "total": total, "page": page })))
}

pub async fn get_service(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let s = ServiceEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "service not found".to_string()))?;
    Ok(Json(ServiceResponse::from(s)))
}

#[derive(Debug, Deserialize)]
pub struct CreateServiceBody {
    pub project_id: Uuid,
    pub slug: String,
    pub name: String,
    pub deploy_type: String,
    pub repository_url: Option<String>,
    pub branch: String,
    pub root_directory: Option<String>,
}

pub async fn create_service(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<CreateServiceBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let now = chrono::Utc::now();
    let new_svc = service::ActiveModel {
        id: Set(Uuid::new_v4()),
        project_id: Set(body.project_id),
        slug: Set(body.slug),
        name: Set(body.name),
        deploy_type: Set(body.deploy_type),
        repository_url: Set(body.repository_url),
        branch: Set(body.branch),
        root_directory: Set(body.root_directory.unwrap_or_default()),
        custom_domains: Set(serde_json::Value::Array(vec![])),
        public_domain_hidden: Set(false),
        created_at: Set(now.into()),
        updated_at: Set(now.into()),
        ..Default::default()
    };
    let inserted = new_svc.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::CREATED, Json(ServiceResponse::from(inserted))))
}

pub async fn delete_service(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    ServiceEntity::delete_by_id(id).exec(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Debug, Serialize)]
pub struct EnvVarResponse {
    pub id: i32,
    pub service_id: Uuid,
    pub key: String,
    pub value: Option<String>,  // redacted unless ?reveal=true
    pub source: String,
    pub is_build_arg: bool,
}

#[derive(Debug, Deserialize)]
pub struct ListEnvVarsQuery {
    pub reveal: Option<bool>,
}

pub async fn list_env_vars(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
    Query(q): Query<ListEnvVarsQuery>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let query = EnvVarEntity::find()
        .filter(environment_variable::Column::ServiceId.eq(service_id));
    let envs = query.all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let reveal = q.reveal.unwrap_or(false);
    let resp: Vec<EnvVarResponse> = envs.into_iter().map(|e| {
        let value = if reveal { Some(e.value) } else { None };
        EnvVarResponse {
            id: e.id,
            service_id: e.service_id,
            key: e.key,
            value,
            source: e.source,
            is_build_arg: e.is_build_arg,
        }
    }).collect();
    Ok(Json(resp))
}

#[derive(Debug, Serialize)]
pub struct DeploymentSummary {
    pub id: Uuid,
    pub service_id: Uuid,
    pub commit_hash: String,
    pub status: String,
    pub is_rollback: bool,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub finished_at: Option<chrono::DateTime<chrono::Utc>>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

impl From<deployment::Model> for DeploymentSummary {
    fn from(m: deployment::Model) -> Self {
        Self {
            id: m.id,
            service_id: m.service_id,
            commit_hash: m.commit_hash,
            status: m.status,
            is_rollback: m.is_rollback,
            started_at: m.started_at.map(|t| t.with_timezone(&chrono::Utc)),
            finished_at: m.finished_at.map(|t| t.with_timezone(&chrono::Utc)),
            created_at: m.created_at.with_timezone(&chrono::Utc),
        }
    }
}

pub async fn get_latest_deployment(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let dep = deployment::Entity::find()
        .filter(deployment::Column::ServiceId.eq(service_id))
        .order_by_desc(deployment::Column::CreatedAt)
        .one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map(DeploymentSummary::from);
    match dep {
        Some(d) => Ok(Json(d)),
        None => Err((StatusCode::NOT_FOUND, "no deployments yet".to_string())),
    }
}
