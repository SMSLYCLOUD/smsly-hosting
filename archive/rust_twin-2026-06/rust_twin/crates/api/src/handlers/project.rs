use axum::{
    extract::State,
    http::StatusCode,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use axum::extract::Path;
use redis::AsyncCommands;
use cn_core::entities::{project, service, deployment};
use crate::{AppState, middleware::AuthUser};

#[derive(Serialize)]
pub struct ProjectResponse {
    pub id: Uuid,
    pub name: String,
    pub slug: String,
    pub description: String,
    pub is_default: bool,
    pub owner_id: i32,
}

impl From<project::Model> for ProjectResponse {
    fn from(model: project::Model) -> Self {
        Self {
            id: model.id,
            name: model.name,
            slug: model.slug,
            description: model.description,
            is_default: model.is_default,
            owner_id: model.owner_id,
        }
    }
}

pub async fn list_projects(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser, // Enforces authentication middleware automatically
) -> Result<Json<Vec<ProjectResponse>>, (StatusCode, String)> {
    // Only return projects belonging to the authenticated user
    let projects = project::Entity::find()
        .filter(project::Column::OwnerId.eq(auth_user.id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let response = projects.into_iter().map(ProjectResponse::from).collect();

    Ok(Json(response))
}

#[derive(Deserialize)]
pub struct CreateProjectRequest {
    pub name: String,
    pub slug: String,
    pub description: Option<String>,
}

pub async fn create_project(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser,
    Json(payload): Json<CreateProjectRequest>,
) -> Result<(StatusCode, Json<ProjectResponse>), (StatusCode, String)> {

    // 1. Enforce uniqueness: Ensure owner + slug is unique (Django Meta constraint)
    let existing = project::Entity::find()
        .filter(project::Column::OwnerId.eq(auth_user.id))
        .filter(project::Column::Slug.eq(&payload.slug))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if existing.is_some() {
        return Err((
            StatusCode::BAD_REQUEST,
            "A project with this slug already exists for the owner".to_string(),
        ));
    }

    // 2. Validate the user exists (skipped, guaranteed by AuthUser extractor)

    // 3. Create active model
    let new_project = project::ActiveModel {
        id: Set(Uuid::new_v4()),
        owner_id: Set(auth_user.id),
        name: Set(payload.name),
        slug: Set(payload.slug),
        description: Set(payload.description.unwrap_or_default()),
        icon_emoji: Set("📦".to_string()),
        color: Set("#6366f1".to_string()),
        is_default: Set(false),
        created_at: Set(chrono::Utc::now().into()),
        updated_at: Set(chrono::Utc::now().into()),
        ..Default::default()
    };

    // 4. Insert into database
    let inserted = new_project
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok((StatusCode::CREATED, Json(ProjectResponse::from(inserted))))
}

#[derive(Deserialize)]
pub struct TriggerDeployRequest {
    pub service_id: Uuid,
    pub commit_hash: String,
}

pub async fn trigger_deploy(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser,
    Path(project_id): Path<Uuid>,
    Json(payload): Json<TriggerDeployRequest>,
) -> Result<(StatusCode, String), (StatusCode, String)> {

    // 1. Validate project ownership
    let project_opt = project::Entity::find_by_id(project_id)
        .filter(project::Column::OwnerId.eq(auth_user.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if project_opt.is_none() {
        return Err((StatusCode::NOT_FOUND, "Project not found or access denied".to_string()));
    }

    // 2. Validate service belongs to project
    let svc_opt = service::Entity::find_by_id(payload.service_id)
        .filter(service::Column::ProjectId.eq(project_id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if svc_opt.is_none() {
        return Err((StatusCode::NOT_FOUND, "Service not found in this project".to_string()));
    }

    // 3. Create a new Deployment record (PENDING)
    let new_deploy = deployment::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(payload.service_id),
        commit_hash: Set(payload.commit_hash.clone()),
        status: Set("PENDING".to_string()),
        is_rollback: Set(false),
        created_at: Set(chrono::Utc::now().into()),
        ..Default::default()
    };

    let inserted_deploy = new_deploy
        .insert(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // 4. Construct the Task Payload matching the Worker's `Task` Enum
    let task_payload = serde_json::json!({
        "type": "SmartDeploy",
        "payload": {
            "project_id": project_id,
            "deployment_id": inserted_deploy.id,
            "commit_hash": payload.commit_hash
        }
    });

    // 5. Push task to Redis Queue `grid:tasks:default`
    let mut redis_conn = state.redis.get_multiplexed_async_connection().await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis connection failed: {}", e))
    })?;

    let queue_name = "grid:tasks:default";
    redis_conn.lpush::<_, _, ()>(queue_name, task_payload.to_string()).await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Failed to push task to queue: {}", e))
    })?;

    Ok((StatusCode::ACCEPTED, format!("Deployment {} triggered successfully", inserted_deploy.id)))
}

pub async fn get_project(
    State(state): State<Arc<AppState>>,
    auth_user: AuthUser,
    Path(project_id): Path<Uuid>,
) -> Result<Json<ProjectResponse>, (StatusCode, String)> {
    let project_opt = project::Entity::find_by_id(project_id)
        .filter(project::Column::OwnerId.eq(auth_user.id))
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let project_opt = project_opt
        .ok_or_else(|| (StatusCode::NOT_FOUND, "Project not found or access denied".to_string()))?;

    Ok(Json(ProjectResponse::from(project_opt)))
}

pub async fn list_project_services(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(project_id): Path<Uuid>,
) -> Result<Json<Vec<crate::handlers::service::ServiceResponse>>, (StatusCode, String)> {
    let services = service::Entity::find()
        .filter(service::Column::ProjectId.eq(project_id))
        .all(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let resp: Vec<crate::handlers::service::ServiceResponse> = services
        .into_iter()
        .map(Into::into)
        .collect();

    Ok(Json(resp))
}
