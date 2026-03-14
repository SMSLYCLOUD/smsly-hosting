use axum::{
    extract::State,
    http::StatusCode,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use cn_core::entities::{project, user};
use crate::AppState;

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
) -> Result<Json<Vec<ProjectResponse>>, (StatusCode, String)> {
    let projects = project::Entity::find()
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
    pub owner_id: i32,
}

pub async fn create_project(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreateProjectRequest>,
) -> Result<(StatusCode, Json<ProjectResponse>), (StatusCode, String)> {

    // 1. Enforce uniqueness: Ensure owner + slug is unique (Django Meta constraint)
    let existing = project::Entity::find()
        .filter(project::Column::OwnerId.eq(payload.owner_id))
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

    // 2. Validate the user exists (simulate Foreign Key constraint validation gracefully)
    let user_exists = user::Entity::find_by_id(payload.owner_id)
        .one(&state.db)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if user_exists.is_none() {
        return Err((StatusCode::BAD_REQUEST, "Owner does not exist".to_string()));
    }

    // 3. Create active model
    let new_project = project::ActiveModel {
        id: Set(Uuid::new_v4()),
        owner_id: Set(payload.owner_id),
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
