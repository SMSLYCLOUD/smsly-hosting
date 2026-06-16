//! Domain HTTP handlers.

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
use cn_core::entities::domain;
use cn_core::entities::domain::Entity as DomainEntity;

#[derive(Debug, Serialize)]
pub struct DomainResponse {
    pub id: Uuid,
    pub service_id: Uuid,
    pub domain: String,
    pub is_primary: bool,
    pub ssl_status: String,
    pub ssl_provider: String,
    pub ssl_expires_at: Option<chrono::DateTime<Utc>>,
    pub verification_method: String,
    pub created_at: chrono::DateTime<Utc>,
}

impl From<domain::Model> for DomainResponse {
    fn from(d: domain::Model) -> Self {
        Self {
            id: d.id,
            service_id: d.service_id,
            domain: d.domain,
            is_primary: d.is_primary,
            ssl_status: d.ssl_status,
            ssl_provider: d.ssl_provider,
            ssl_expires_at: d.ssl_expires_at.map(|dt| dt.with_timezone(&Utc)),
            verification_method: d.verification_method,
            created_at: d.created_at.with_timezone(&Utc),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct CreateDomainBody {
    pub service_id: Uuid,
    pub domain: String,
    pub is_primary: Option<bool>,
}

pub async fn list_domains(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let domains = DomainEntity::find()
        .filter(domain::Column::ServiceId.eq(service_id))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<DomainResponse> = domains.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn create_domain(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<CreateDomainBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let now = Utc::now();
    let verification_token = generate_acme_token();
    let new_domain = domain::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(body.service_id),
        domain: Set(body.domain),
        is_primary: Set(body.is_primary.unwrap_or(false)),
        ssl_status: Set("pending".to_string()),
        ssl_provider: Set("letsencrypt".to_string()),
        ssl_expires_at: Set(None),
        ssl_certificate_path: Set(None),
        verification_method: Set("http-01".to_string()),
        verification_token: Set(Some(verification_token)),
        last_verified_at: Set(None),
        created_at: Set(now.into()),
        updated_at: Set(now.into()),
    };
    let inserted = new_domain.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok((StatusCode::CREATED, Json(DomainResponse::from(inserted))))
}

pub async fn delete_domain(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    DomainEntity::delete_by_id(id).exec(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn verify_domain(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let d = DomainEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "domain not found".to_string()))?;
    let mut active: domain::ActiveModel = d.into();
    active.ssl_status = Set("provisioning".to_string());
    active.last_verified_at = Set(Some(Utc::now().into()));
    active.updated_at = Set(Utc::now().into());
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({
        "status": "provisioning",
        "note": "actual ACME validation not implemented in this batch",
    })))
}

fn generate_acme_token() -> String {
    use uuid::Uuid;
    Uuid::new_v4().simple().to_string()
}
