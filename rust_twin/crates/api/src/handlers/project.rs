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
