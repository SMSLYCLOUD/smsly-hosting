//! Addon HTTP handlers — list, create, provision, deprovision, status.

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::Utc;
use std::sync::Arc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::addon;
use cn_core::entities::addon::Entity as AddonEntity;
use cn_core::entities::service::Entity as ServiceEntity;

#[derive(Debug, Serialize)]
pub struct AddonResponse {
    pub id: Uuid,
    pub project_id: Uuid,
    pub service_id: Uuid,
    pub name: String,
    pub addon_type: String,
    pub status: String,
    pub connection_url: Option<String>,
    pub container_id: Option<String>,
    pub created_at: chrono::DateTime<Utc>,
    pub updated_at: chrono::DateTime<Utc>,
}

impl From<addon::Model> for AddonResponse {
    fn from(a: addon::Model) -> Self {
        Self {
            id: a.id,
            project_id: a.project_id.unwrap_or_default(),
            service_id: a.service_id,
            name: a.name,
            addon_type: a.addon_type,
            status: a.status,
            connection_url: a.connection_url,
            container_id: a.container_id,
            created_at: a.created_at.with_timezone(&Utc),
            updated_at: a.updated_at.with_timezone(&Utc),
        }
    }
}

pub async fn list_addons(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let addons = AddonEntity::find()
        .filter(addon::Column::ServiceId.eq(service_id))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<AddonResponse> = addons.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn get_addon(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let a = AddonEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "addon not found".to_string()))?;
    Ok(Json(AddonResponse::from(a)))
}

#[derive(Debug, Deserialize)]
pub struct CreateAddonBody {
    pub service_id: Uuid,
    pub name: String,
    pub addon_type: String,
}

/// Resolve the docker image and port for a given addon type.
/// In production this would come from the addon_template table; for now
/// it's a hardcoded map covering the most common cases.
fn resolve_addon_spec(addon_type: &str) -> Result<(&'static str, u16), String> {
    let lc = addon_type.to_lowercase();
    let lc = lc.as_str();
    match lc {
        "postgres" | "postgresql" | "postgresql-16" | "postgresql-15" | "postgresql-14" => Ok(("postgres:16-alpine", 5432)),
        "redis" | "redis-7" | "redis-6" => Ok(("redis:7-alpine", 6379)),
        "mysql" | "mariadb" | "mysql-8" => Ok(("mysql:8.0", 3306)),
        "mongodb" | "mongo" | "mongodb-7" => Ok(("mongo:7", 27017)),
        "clickhouse" | "clickhouse-24" => Ok(("clickhouse/clickhouse-server:24.3", 8123)),
        "rabbitmq" => Ok(("rabbitmq:3-management", 5672)),
        "kafka" => Ok(("confluentinc/cp-kafka:7.5.0", 9092)),
        "minio" | "s3" => Ok(("minio/minio:latest", 9000)),
        "meilisearch" => Ok(("getmeili/meilisearch:v1.6", 7700)),
        "typesense" => Ok(("typesense/typesense:0.25.1", 8108)),
        "plausible" | "plausible/analytics" => Ok(("plausible/analytics:latest", 8000)),
        "umami" => Ok(("ghcr.io/mikecao/umami/umami:latest", 3000)),
        "metabase" => Ok(("metabase/metabase:latest", 3000)),
        "nocodb" => Ok(("nocodb/nocodb:latest", 8080)),
        "n8n" => Ok(("n8nio/n8n:latest", 5678)),
        "ghost" => Ok(("ghost:5", 2368)),
        "wordpress" | "wp" => Ok(("wordpress:latest", 80)),
        _ => Err(format!("unsupported addon type: {}", addon_type)),
    }
}

pub async fn create_addon(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<CreateAddonBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let (_image, _port) = resolve_addon_spec(&body.addon_type)
        .map_err(|e| (StatusCode::BAD_REQUEST, e))?;
    let svc = ServiceEntity::find_by_id(body.service_id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "service not found".to_string()))?;
    let now: chrono::DateTime<Utc> = Utc::now();
    let new_addon = addon::ActiveModel {
        id: Set(Uuid::new_v4()),
        project_id: Set(Some(svc.project_id)),
        service_id: Set(body.service_id),
        name: Set(body.name),
        addon_type: Set(body.addon_type.to_uppercase()),
        status: Set("PROVISIONING".to_string()),
        connection_url: Set(None),
        container_id: Set(None),
        created_at: Set(now.into()),
        updated_at: Set(now.into()),
    };
    let inserted = new_addon.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::ACCEPTED, Json(AddonResponse::from(inserted))))
}

pub async fn deprovision_addon(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let a = AddonEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "addon not found".to_string()))?;
    let mut active: addon::ActiveModel = a.into();
    active.status = Set("DEPROVISIONING".to_string());
    active.updated_at = Set(Utc::now().into());
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "deprovisioning" })))
}

pub async fn delete_addon(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let a = AddonEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "addon not found".to_string()))?;
    if a.status == "PROVISIONING" || a.status == "DEPROVISIONING" {
        return Err((StatusCode::CONFLICT, "wait for current operation to complete".to_string()));
    }
    let active: addon::ActiveModel = a.into();
    active.delete(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn get_addon_logs(
    State(_state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    Ok(Json(serde_json::json!({
        "addon_id": id,
        "logs": [
            "[stub] addon log streaming not implemented",
            "in production this would docker logs <container_id> --tail 100 --follow",
        ],
    })))
}

pub async fn get_supported_addon_types() -> impl IntoResponse {
    Json(serde_json::json!({
        "types": [
            {"type": "postgres",  "default_port": 5432,  "image": "postgres:16-alpine"},
            {"type": "redis",     "default_port": 6379,  "image": "redis:7-alpine"},
            {"type": "mysql",     "default_port": 3306,  "image": "mysql:8.0"},
            {"type": "mongodb",   "default_port": 27017, "image": "mongo:7"},
            {"type": "clickhouse","default_port": 8123,  "image": "clickhouse/clickhouse-server:24.3"},
            {"type": "rabbitmq",  "default_port": 5672,  "image": "rabbitmq:3-management"},
            {"type": "kafka",     "default_port": 9092,  "image": "confluentinc/cp-kafka:7.5.0"},
            {"type": "minio",     "default_port": 9000,  "image": "minio/minio:latest"},
            {"type": "meilisearch","default_port": 7700, "image": "getmeili/meilisearch:v1.6"},
            {"type": "plausible", "default_port": 8000,  "image": "plausible/analytics:latest"},
            {"type": "umami",     "default_port": 3000,  "image": "ghcr.io/mikecao/umami/umami:latest"},
            {"type": "metabase",  "default_port": 3000,  "image": "metabase/metabase:latest"},
            {"type": "nocodb",    "default_port": 8080,  "image": "nocodb/nocodb:latest"},
            {"type": "n8n",       "default_port": 5678,  "image": "n8nio/n8n:latest"},
            {"type": "ghost",     "default_port": 2368,  "image": "ghost:5"},
            {"type": "wordpress", "default_port": 80,    "image": "wordpress:latest"},
        ],
    }))
}
