//! Observability HTTP handlers — Prometheus metrics, health probes, audit log.

use axum::{
    extract::State,
    http::{header, StatusCode},
    response::IntoResponse,
    Json,
};
use sea_orm::{ColumnTrait, EntityTrait, QueryFilter};
use serde::Serialize;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;
use chrono::Utc;

use crate::AppState;
use cn_core::entities::deployment;
use cn_core::entities::service;

static APP_START_TIME: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
static HTTP_REQUESTS_TOTAL: AtomicU64 = AtomicU64::new(0);
static HTTP_ERRORS_TOTAL: AtomicU64 = AtomicU64::new(0);

pub fn inc_http_request() { HTTP_REQUESTS_TOTAL.fetch_add(1, Ordering::Relaxed); }
pub fn inc_http_error() { HTTP_ERRORS_TOTAL.fetch_add(1, Ordering::Relaxed); }

pub async fn metrics(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let user_count = cn_core::entities::user::Entity::find().all(&state.db).await
        .map(|u| u.len() as i64).unwrap_or(0);
    let project_count = cn_core::entities::project::Entity::find().all(&state.db).await
        .map(|p| p.len() as i64).unwrap_or(0);
    let service_count = service::Entity::find().all(&state.db).await
        .map(|s| s.len() as i64).unwrap_or(0);
    let deployment_count = deployment::Entity::find().all(&state.db).await
        .map(|d| d.len() as i64).unwrap_or(0);
    let running = deployment::Entity::find()
        .filter(deployment::Column::Status.eq("RUNNING"))
        .all(&state.db).await
        .map(|d| d.len() as i64).unwrap_or(0);
    let failed_24h = deployment::Entity::find()
        .filter(deployment::Column::Status.eq("DEPLOY_FAILED"))
        .filter(deployment::Column::CreatedAt.gt(Utc::now() - chrono::Duration::hours(24)))
        .all(&state.db).await
        .map(|d| d.len() as i64).unwrap_or(0);
    let http_requests = HTTP_REQUESTS_TOTAL.load(Ordering::Relaxed);
    let http_errors = HTTP_ERRORS_TOTAL.load(Ordering::Relaxed);
    let uptime = APP_START_TIME.get_or_init(Instant::now).elapsed().as_secs();
    let body = format!(
        "# HELP smsly_info Build info\n\
         # TYPE smsly_info gauge\n\
         smsly_info{{version=\"0.1.0\"}} 1\n\
         # HELP smsly_uptime_seconds Uptime in seconds\n\
         # TYPE smsly_uptime_seconds gauge\n\
         smsly_uptime_seconds {uptime}\n\
         # HELP smsly_users_total Total registered users\n\
         # TYPE smsly_users_total gauge\n\
         smsly_users_total {user_count}\n\
         # HELP smsly_projects_total Total projects\n\
         # TYPE smsly_projects_total gauge\n\
         smsly_projects_total {project_count}\n\
         # HELP smsly_services_total Total services\n\
         # TYPE smsly_services_total gauge\n\
         smsly_services_total {service_count}\n\
         # HELP smsly_deployments_total Total deployments\n\
         # TYPE smsly_deployments_total gauge\n\
         smsly_deployments_total {deployment_count}\n\
         # HELP smsly_deployments_running Currently running deployments\n\
         # TYPE smsly_deployments_running gauge\n\
         smsly_deployments_running {running}\n\
         # HELP smsly_deployments_failed_24h Failed deployments in last 24h\n\
         # TYPE smsly_deployments_failed_24h counter\n\
         smsly_deployments_failed_24h {failed_24h}\n\
         # HELP smsly_http_requests_total Total HTTP requests served\n\
         # TYPE smsly_http_requests_total counter\n\
         smsly_http_requests_total {http_requests}\n\
         # HELP smsly_http_errors_total Total HTTP errors\n\
         # TYPE smsly_http_errors_total counter\n\
         smsly_http_errors_total {http_errors}\n"
    );
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/plain; version=0.0.4")],
        body,
    )
}

pub async fn health_live() -> impl IntoResponse {
    // Liveness — process is alive, don't check dependencies
    (StatusCode::OK, Json(serde_json::json!({ "status": "ok" })))
}

pub async fn health_ready(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    // Readiness — DB must be reachable
    let db_ok = cn_core::entities::user::Entity::find()
        .all(&state.db).await
        .is_ok();
    if db_ok {
        (StatusCode::OK, Json(serde_json::json!({
            "status": "ok",
            "db": "up",
        })))
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({
            "status": "degraded",
            "db": "down",
        })))
    }
}

pub async fn health() -> impl IntoResponse {
    // Combined — just "OK" (matches P3's main.rs)
    (StatusCode::OK, [("content-type", "text/plain")], "OK")
}

#[derive(Debug, Serialize)]
pub struct VersionInfo {
    pub version: String,
    pub commit: String,
    pub build_date: String,
    pub api_version: String,
}

pub async fn version(State(_state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(VersionInfo {
        version: env!("CARGO_PKG_VERSION").to_string(),
        commit: option_env!("SMSLY_GIT_COMMIT").unwrap_or("unknown").to_string(),
        build_date: option_env!("SMSLY_BUILD_DATE").unwrap_or("unknown").to_string(),
        api_version: "v1".to_string(),
    })
}
