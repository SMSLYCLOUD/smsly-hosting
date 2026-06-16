//! Backup HTTP handlers — list, create, verify, restore, download, delete.

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use sea_orm::{ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::Utc;
use std::sync::Arc;

use crate::AppState;
use crate::middleware::AuthUser;
use cn_core::entities::backup_record;
use cn_core::entities::backup_record::Entity as BackupEntity;
use cn_core::entities::service::Entity as ServiceEntity;

#[derive(Debug, Serialize)]
pub struct BackupResponse {
    pub id: Uuid,
    pub service_id: Uuid,
    pub storage_backend: String,
    pub path: String,
    pub size_bytes: i64,
    pub sha256: String,
    pub encryption_algo: String,
    pub encryption_key_id: String,
    pub status: String,
    pub created_at: chrono::DateTime<Utc>,
    pub verified_at: Option<chrono::DateTime<Utc>>,
    pub expires_at: Option<chrono::DateTime<Utc>>,
}

impl From<backup_record::Model> for BackupResponse {
    fn from(b: backup_record::Model) -> Self {
        Self {
            id: b.id,
            service_id: b.service_id,
            storage_backend: b.storage_backend,
            path: b.path,
            size_bytes: b.size_bytes,
            sha256: b.sha256,
            encryption_algo: b.encryption_algo,
            encryption_key_id: b.encryption_key_id,
            status: b.status,
            created_at: b.created_at.into(),
            verified_at: b.verified_at.map(Into::into),
            expires_at: b.expires_at.map(Into::into),
        }
    }
}

pub async fn list_backups(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let backups = BackupEntity::find()
        .filter(backup_record::Column::ServiceId.eq(service_id))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let resp: Vec<BackupResponse> = backups.into_iter().map(Into::into).collect();
    Ok(Json(resp))
}

pub async fn get_backup(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let b = BackupEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "backup not found".to_string()))?;
    Ok(Json(BackupResponse::from(b)))
}

#[derive(Debug, Deserialize)]
pub struct CreateBackupBody {
    pub storage_backend: Option<String>,  // default "s3"
    pub encryption_key_id: Option<String>, // default "default"
}

pub async fn create_backup(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(service_id): Path<Uuid>,
    Json(body): Json<CreateBackupBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    // Verify service exists
    let svc = ServiceEntity::find_by_id(service_id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "service not found".to_string()))?;
    let now = Utc::now();
    // In a real implementation, this would:
    // 1. Snapshot the service's volumes (docker commit or file tar)
    // 2. Encrypt with Fernet using the key_id
    // 3. Upload to the storage backend
    // 4. Compute SHA-256 of the encrypted blob
    // For now, create a stub record
    let new_backup = backup_record::ActiveModel {
        id: Set(Uuid::new_v4()),
        service_id: Set(service_id),
        storage_backend: Set(body.storage_backend.unwrap_or_else(|| "s3".to_string())),
        path: Set(format!("s3://smsly-backups/{}/{}-{}.enc",
            service_id, svc.slug, now.timestamp())),
        size_bytes: Set(0),  // stub: would be the actual size
        sha256: Set("pending".to_string()),  // stub
        encryption_algo: Set("AES-256-GCM".to_string()),
        encryption_key_id: Set(body.encryption_key_id.unwrap_or_else(|| "default".to_string())),
        status: Set("pending".to_string()),
        created_at: Set(now.into()),
        verified_at: Set(None),
        expires_at: Set(Some((now + chrono::Duration::days(90)).into())),  // 90-day retention
    };
    use sea_orm::ActiveModelTrait;
    let inserted = new_backup.insert(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    // In a real implementation, enqueue a Celery task here
    Ok((StatusCode::ACCEPTED, Json(BackupResponse::from(inserted))))
}

pub async fn verify_backup(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let b = BackupEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "backup not found".to_string()))?;
    // In a real implementation, recompute SHA-256 and compare
    let mut active: backup_record::ActiveModel = b.into();
    active.status = Set("verified".to_string());
    active.verified_at = Set(Some(Utc::now().into()));
    use sea_orm::ActiveModelTrait;
    active.update(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(serde_json::json!({ "status": "verified" })))
}

#[derive(Debug, Deserialize)]
pub struct RestoreBackupBody {
    pub target_service_id: Option<Uuid>,  // default: same service
}

pub async fn restore_backup(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
    Json(body): Json<RestoreBackupBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let b = BackupEntity::find_by_id(id).one(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .ok_or_else(|| (StatusCode::NOT_FOUND, "backup not found".to_string()))?;
    if b.status != "verified" {
        return Err((StatusCode::BAD_REQUEST, "backup must be verified before restore".to_string()));
    }
    let target_id = body.target_service_id.unwrap_or(b.service_id);
    // In a real implementation:
    // 1. Download the encrypted blob from storage
    // 2. Decrypt
    // 3. Stop the running service
    // 4. Restore the volumes
    // 5. Start the service
    // For now, just return a stub
    Ok((StatusCode::ACCEPTED, Json(serde_json::json!({
        "status": "restoring",
        "target_service_id": target_id,
        "note": "actual restore not implemented in this batch",
    }))))
}

pub async fn download_backup(
    State(_state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<Response, (StatusCode, String)> {
    // In a real implementation, stream the encrypted blob from storage
    // For now, return a stub
    let body = Body::from(format!("# stub backup {}\n# actual content would be the encrypted blob\n", id));
    let response = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::CONTENT_DISPOSITION, format!("attachment; filename=\"backup-{}.enc\"", id))
        .body(body)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(response)
}

pub async fn delete_backup(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Path(id): Path<Uuid>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    BackupEntity::delete_by_id(id).exec(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Debug, Deserialize)]
pub struct RetentionPolicy {
    pub days: i64,  // backups older than this are deleted
}

pub async fn apply_retention(
    State(state): State<Arc<AppState>>,
    _auth: AuthUser,
    Json(body): Json<RetentionPolicy>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let cutoff = Utc::now() - chrono::Duration::days(body.days);
    // Find all backups older than cutoff
    let old = BackupEntity::find()
        .filter(backup_record::Column::CreatedAt.lt(cutoff))
        .all(&state.db).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let count = old.len();
    for b in old {
        // In a real implementation, also delete from storage backend
        BackupEntity::delete_by_id(b.id).exec(&state.db).await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    }
    Ok(Json(serde_json::json!({
        "deleted": count,
        "cutoff": cutoff,
    })))
}
